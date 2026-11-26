import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cloneDocumentStyles } from '../../utils/pipWindow';
import axios from 'axios';
import {
  Bell,
  CalendarClock,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft as LucideChevronLeft,
  ChevronRight as LucideChevronRight,
  Download,
  GripVertical,
  Inbox,
  Layers,
  Link2,
  ListTodo,
  LoaderCircle,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Palette,
  PanelRightOpen,
  Pencil,
  PictureInPicture2,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  StickyNote,
  Timer,
  Trash2,
  Undo2,
} from 'lucide-react';
import { normalizeRole, isAdminLikeRole, isSupervisorRole } from '../../utils/roles';
import { rankAndFileTasksAllowed } from '../../utils/departmentViews';
import FaIcon from '../common/FaIcon';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import FullscreenSheet from '../common/FullscreenSheet';
import MobileActionSheet from '../common/MobileActionSheet';
import useOverlayDismiss from '../common/useOverlayDismiss';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import TaskBoardWorkspace from './TaskBoardWorkspace';
import PriorityScoreControl, { PriorityScoreDot } from './PriorityScoreControl';
import TaskDateTimeField from './TaskDateTimeField';
import TaskDeadlineField from './TaskDeadlineField';
import {
  deadlineDurationFields,
  deadlineSummary,
  isLocalDateTimePast,
  minutesBetweenLocal,
  shiftLocalDateTime,
  splitLocalDateTime,
} from './taskDateTime';
import { PRIORITY_SCORE_HINT, awaitsPriorityScore, priorityScoreOf, priorityScoreTitle, scoreBoardMove, unscoreMovesToNew } from './backlogOrder';
import { BriefIssueNav, ProductionBriefFields, ProductionBriefView, useBriefIssueNav } from './ProductionBriefUi';
import {
  PRODUCTION_TAG,
  briefFromTask,
  briefToPayload,
  emptyProductionBrief,
  productionAllowedFor,
  productionBriefIssues,
  productionPersonIds,
} from './productionBrief';
// Телефонный слой раздела: всё в нём заперто на body.mobile-shell.
import './tasks-mobile.css';
import { boardQueryParams, scopeQueryParams } from './boardQuery';
import { planTaskFocus, shouldOpenFetchedTask } from './taskFocus';
import {
  TASK_MAX_ASSIGNEES,
  isTaskAssignee,
  taskAssigneeIds,
  taskAssignees,
  taskAssigneesLabel,
} from './taskAssignees';
import { stripTechnicalQueryParams } from '../../utils/urlHygiene';
import {
  TASK_PROJECT_ALL,
  TASK_SECTION_ALL,
  allBoardAssigneeIds,
  assigneesAfterProjectChange,
  boardAssignees,
  boardWithoutExecutors,
  buildProjectOptions,
  buildSectionOptions,
  composerProjectStorageKey,
  creatableProjects,
  findProject,
  hasBoardPeople,
  hasProjectBoards,
  isAllBoardAssigneesSelection,
  keepAssigneesForProject,
  keepWatchersForProject,
  mergeSelectedProject,
  normalizeTaskKey,
  projectLabel,
  projectQueryParams,
  projectStorageKey,
  resolveComposerProject,
  resolveInitialProject,
  resolveSectionFilter,
  sectionQueryParams,
  sectionsOf,
  taskKeyOf,
  watcherCandidates,
} from './taskProjects';
import TaskProjectsSheet from './TaskProjectsSheet';
import TaskWatchersSheet from './TaskWatchersSheet';
import {
  ACTION_NEED_META,
  actionNeedSeenKey,
  collectTaskActionNeeds,
  countUnseenActionNeeds,
  groupTaskActionNeeds,
  isActionNeedSeen,
  taskActionNeed,
} from './taskActionNeeds';
import { IosLightbox, IosMenu, IosToggle } from '../ui/ios';
import { isPreviewablePhoto, splitTaskFiles } from './taskPhotos';
import useTaskPhotoPreviews from './useTaskPhotoPreviews';
import FilePreviewSheet, { TypeBadge, saveBlobAs } from '../file_preview/FilePreviewSheet';
import { previewKind } from '../file_preview/fileKinds.js';
import { previewCache } from '../file_preview/previewCache.js';

/* Шрифты Google разделу больше не нужны: он набран системным шрифтом Apple, как
   весь сайт. Syne и DM Sans подключают только «Курсы» (common/sectionFonts.js). */

/* ─── Styles ─── */
const styleTag = document.createElement('style');
styleTag.textContent = `
  /* Раздел живёт в дизайн-системе сайта: системный шрифт Apple (SF Pro), серое
     полотно macOS, синяя главная кнопка, цвет — только там, где он что-то значит. */
  .tv-root * { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif; box-sizing: border-box; }
  .tv-root h1, .tv-root h2, .tv-root h3, .tv-root .heading { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif; }

  /* Бэклог/доска/таймлайн живут в дизайн-системе сайта (SF Pro), а не в editorial-типографике раздела. */
  .tv-root .tb-scope, .tv-root .tb-scope * {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif;
  }

  .tv-root {
    --bg: #f5f5f7;
    --surface: #ffffff;
    --surface-2: #f8fafc;
    --border: #e2e8f0;
    --border-strong: #cbd5e1;
    --ink: #0f172a;
    --ink-2: #475569;
    --ink-3: #94a3b8;
    --accent: #2563eb;
    --accent-fg: #ffffff;
    /* Выбор — мягкий голубой, как везде на сайте; серая заливка — вторичные кнопки и капсулы. */
    --select-bg: #eef4ff;
    --select-border: #bfd4fb;
    --select-ink: #1d4ed8;
    --fill: #f1f5f9;
    --fill-strong: #e2e8f0;
    --amber: #d97706;
    --indigo: #4338ca;
    --emerald: #059669;
    --rose: #e11d48;
    --blue: #2563eb;
    --shadow-sm: 0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
    --shadow-md: 0 4px 16px rgba(0,0,0,.08), 0 2px 6px rgba(0,0,0,.05);
    --shadow-lg: 0 20px 60px rgba(0,0,0,.14), 0 8px 24px rgba(0,0,0,.08);
    --radius: 16px;
    --radius-sm: 12px;
    --radius-xs: 6px;
    /* Системные заливки и линии iOS (новый вид 16.11): полупрозрачные, поэтому
       ложатся и на белое, и на серое; тёмные значения — в dark_theme_chrome.css. */
    --ios-fill: rgba(118, 118, 128, .12);
    --ios-fill-strong: rgba(118, 118, 128, .2);
    --ios-sep: rgba(60, 60, 67, .18);
    --check-indigo: #a5b4fc;
    --check-amber: #f59e0b;
    --check-rose: #fb7185;
    --check-violet: #8b5cf6;
    --check-emerald: #10b981;
    --state-indigo: #4f46e5;
    --state-amber: #b45309;
    --state-violet: #6d28d9;
    --state-emerald: #047857;
    --state-rose: #be123c;
    background: var(--bg);
    min-height: 100vh;
    padding: 28px 24px;
  }
  /* Полотно раздела — нейтральная подложка macOS на всех вкладках, и на доске тоже. */

  /* ── Top bar ── */
  .tv-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 28px;
    gap: 12px;
    flex-wrap: wrap;
  }
  .tv-topbar-title {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--ink);
    margin: 0;
  }
  .tv-topbar-lead { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .tv-topbar-actions { display: flex; align-items: center; gap: 8px; }

  /* Переключатель проектов — элемент дизайн-системы сайта, а не editorial-шрифт раздела.
     Ширина под самое длинное название («SZOV · СЗоВ — служба заботы о водителях»):
     текущий охват раздела нельзя показывать многоточием. */
  .tv-project-select { width: 312px; }
  /* Фильтр разделов открытой доски — уже переключателя: названия разделов короткие. */
  .tv-section-select { width: 220px; }
  .tv-project-select, .tv-project-select *, .tv-section-select, .tv-section-select * {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif;
  }
  /* Ключ задачи проекта (HR-125): имя задачи, а не её состояние — серым, без рамки. */
  .tv-task-key {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    font-size: 11.5px; font-weight: 600; color: var(--ink-3, #94a3b8);
    font-variant-numeric: tabular-nums; letter-spacing: 0.01em; white-space: nowrap;
  }
  .tv-drawer-key { display: block; margin-bottom: 2px; }

  /* ── Уведомления «задача ждёт вас» ── */
  .tv-inbox { position: relative; display: inline-flex; }
  .tv-inbox-btn { position: relative; padding-right: 12px; }
  .tv-inbox-btn.is-hot { background: #fff1f2; border-color: #fecdd3; color: var(--rose); }
  .tv-inbox-btn.is-hot:hover:not(:disabled) { background: #ffe4e6; border-color: #fda4af; color: var(--rose); }
  .tv-inbox-count {
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 18px; height: 18px; padding: 0 5px;
    border-radius: 99px;
    background: var(--rose); color: #fff;
    font-size: 10.5px; font-weight: 700; line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .tv-inbox-pop {
    position: absolute; top: calc(100% + 8px); right: 0; z-index: 60;
    width: min(384px, calc(100vw - 32px));
    background: var(--surface);
    border-radius: 16px;
    border: 1px solid var(--border);
    box-shadow: 0 18px 48px rgba(15, 23, 42, .16);
    overflow: hidden;
    animation: tvInboxIn .16s cubic-bezier(.22,.9,.3,1);
  }
  @keyframes tvInboxIn {
    from { opacity: 0; transform: translateY(-6px) scale(.985); }
    to   { opacity: 1; transform: none; }
  }
  .tv-inbox-head {
    display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
    padding: 13px 16px 10px;
    border-bottom: 1px solid var(--border);
  }
  .tv-inbox-title { font-size: 14px; font-weight: 600; color: var(--ink); }
  .tv-inbox-sub { font-size: 11.5px; color: var(--ink-3); }
  .tv-inbox-scroll { max-height: min(60vh, 460px); overflow-y: auto; padding: 6px; }
  .tv-inbox-group-label {
    display: flex; align-items: center; gap: 6px;
    padding: 8px 10px 5px;
    font-size: 10px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase;
    color: var(--ink-3);
  }
  .tv-inbox-item {
    width: 100%;
    display: flex; align-items: flex-start; gap: 9px;
    padding: 9px 10px;
    border: 0; border-radius: 10px;
    background: transparent; cursor: pointer; text-align: left;
    transition: background .13s ease;
  }
  .tv-inbox-item:hover { background: var(--surface-2); }
  .tv-inbox-item:active { background: #f0efec; }
  .tv-inbox-dot { width: 7px; height: 7px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }
  .tv-inbox-item-main { min-width: 0; flex: 1; }
  .tv-inbox-item-subject {
    display: block;
    font-size: 13px; font-weight: 500; color: var(--ink);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .tv-inbox-item-meta {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    margin-top: 2px;
    font-size: 11.5px; color: var(--ink-3);
  }
  .tv-inbox-item-due { color: var(--rose); font-weight: 600; }
  /* Просмотренное остаётся в списке (задача-то ждёт), но в счётчик уже не идёт. */
  .tv-inbox-item.is-seen { opacity: .5; }
  .tv-inbox-item.is-seen:hover { opacity: .75; }
  .tv-inbox-item.is-seen .tv-inbox-item-subject { font-weight: 400; }
  .tv-inbox-foot {
    display: flex; justify-content: flex-end;
    padding: 8px 10px;
    border-top: 1px solid var(--border);
    background: var(--surface-2);
  }
  .tv-inbox-mark-all {
    border: 0; background: transparent; cursor: pointer;
    padding: 4px 8px; border-radius: 8px;
    font-size: 12px; font-weight: 500; color: var(--ink-3);
    transition: all .13s ease;
  }
  .tv-inbox-mark-all:hover { color: var(--ink); background: #e8e8ed; }
  .tv-inbox-empty {
    padding: 26px 18px 30px;
    text-align: center;
  }
  .tv-inbox-empty-title { display: block; font-size: 13px; font-weight: 600; color: var(--ink-2); }
  .tv-inbox-empty-sub { display: block; margin-top: 4px; font-size: 11.5px; color: var(--ink-3); }

  /* Карточка, к которой перешли из уведомления. */
  .tb-card-focus {
    box-shadow: 0 0 0 2px #2563eb, 0 6px 18px rgba(37, 99, 235, .18) !important;
  }

  /* Собачка на карточке: задача ждёт шага этого пользователя. Гаснет по прочтении. */
  .tb-at-marker {
    display: inline-grid;
    place-items: center;
    width: 17px; height: 17px;
    flex-shrink: 0;
    border-radius: 50%;
    font-size: 11.5px;
    font-weight: 800;
    line-height: 1;
    animation: tbAtBlink 1.5s ease-in-out infinite;
  }
  @keyframes tbAtBlink {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: .35; transform: scale(.9); }
  }
  @media (prefers-reduced-motion: reduce) {
    .tb-at-marker { animation: none; }
  }

  /* ── Stats strip ── */
  .tv-stats-strip {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
    margin-bottom: 28px;
  }

  /* ── Workspace tabs (Обзор / Бэклог / Доска / Таймлайн) ── */
  .tv-workspace-tabs {
    display: inline-flex;
    gap: 2px;
    padding: 3px;
    margin-bottom: 22px;
    border-radius: 11px;
    background: #e8e8ed;
  }
  .tv-workspace-tab {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    font-size: 13px;
    font-weight: 500;
    color: var(--ink-2);
    cursor: pointer;
    transition: background .15s, color .15s, box-shadow .15s;
    white-space: nowrap;
  }
  .tv-workspace-tab:hover { color: var(--ink); }
  .tv-workspace-tab.is-active {
    background: var(--surface);
    color: var(--ink);
    box-shadow: 0 1px 3px rgba(0,0,0,.10);
  }
  .tv-workspace-tab-count {
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    color: var(--ink-3);
  }

  /* ── Отчёты о проделанной работе ── */
  .tv-reports-header {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 12px; margin-bottom: 10px; flex-wrap: wrap;
  }
  .tv-reports-meta {
    display: flex; gap: 8px; flex-wrap: wrap;
    font-size: 11.5px; color: var(--ink-3);
    font-variant-numeric: tabular-nums;
  }
  .tv-reports-meta .is-over { color: #b45309; }
  .tv-reports-empty { font-size: 12.5px; color: var(--ink-3); line-height: 1.5; margin: 0 0 10px; }
  .tv-reports-list { display: flex; flex-direction: column; gap: 8px; }
  .tv-report-card {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
  }
  .tv-report-card.is-final { border-color: #c7d2fe; background: #f5f6ff; }
  .tv-report-top {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    font-size: 11.5px; color: var(--ink-3); margin-bottom: 6px;
  }
  .tv-report-kind { font-weight: 600; color: var(--ink-2); }
  .tv-report-card.is-final .tv-report-kind { color: var(--indigo); }
  .tv-report-spent {
    padding: 1px 7px; border-radius: 99px;
    background: #e8e8ed; color: var(--ink-2); font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
  .tv-report-actions { display: flex; gap: 8px; margin-left: auto; }
  .tv-report-actions button {
    border: 0; background: none; padding: 0; cursor: pointer;
    font-size: 11.5px; color: var(--ink-3);
  }
  .tv-report-actions button:hover:not(:disabled) { color: var(--ink); text-decoration: underline; }
  .tv-report-actions button:disabled { opacity: .5; cursor: not-allowed; }
  .tv-report-body {
    margin: 0; font-size: 13px; line-height: 1.55;
    color: var(--ink); white-space: pre-wrap; word-break: break-word;
  }
  .tv-report-form { display: flex; flex-direction: column; gap: 8px; }
  .tv-report-form-row { display: flex; gap: 8px; align-items: center; }
  .tv-report-form-row .tv-input { flex: 1; }
  .tv-report-composer { margin-top: 10px; }
  .tv-reports-add { margin-top: 10px; }

  /* ── Уточнения по задаче ── */
  .tv-clar-head {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 12px; margin-bottom: 10px; flex-wrap: wrap;
  }
  /* Метка «работа стоит из-за вопроса». Не badge: рядом уже стоят бейджи
     статуса и срочности, и четвёртая такая же пилюля читалась бы как ещё один
     статус. Точка + подпись в цвете самой причины (#7c3aed — тот же, что у
     «Просят информацию» в панели «Ждут вас»), поэтому в списке, на доске и в
     колоколе это узнаётся как одно и то же состояние. */
  .tv-clar-waiting {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 600; color: #6d28d9;
    white-space: nowrap;
  }
  .tv-clar-waiting::before {
    content: ''; width: 6px; height: 6px; border-radius: 50%;
    background: #7c3aed; flex: none;
  }
  .tv-clar-pending {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 2px 8px; border-radius: 99px;
    background: #f5f3ff; color: #6d28d9; border: 1px solid #ddd6fe;
    font-size: 11px; font-weight: 600;
  }
  .tv-clar-empty { font-size: 12.5px; color: var(--ink-3); line-height: 1.5; margin: 0; }
  .tv-clar-list { display: flex; flex-direction: column; gap: 8px; }
  .tv-clar-item { display: flex; width: 100%; }
  .tv-clar-item.is-owner { justify-content: flex-start; }
  .tv-clar-item.is-assignee { justify-content: flex-end; }
  .tv-clar-bubble {
    display: flex; flex-direction: column; gap: 4px;
    max-width: 85%; padding: 8px 12px;
    font-size: 12.5px; word-break: break-word;
    background: #eef2ff; border: 1px solid #c7d2fe;
    border-radius: 4px 12px 12px 12px;
  }
  .tv-clar-item.is-assignee .tv-clar-bubble {
    background: var(--surface-2); border-color: var(--border);
    border-radius: 12px 4px 12px 12px;
  }
  /* Открытый запрос — единственное цветное состояние: пока не ответят, работа стоит. */
  .tv-clar-item .tv-clar-bubble.is-open { background: #f5f3ff; border-color: #ddd6fe; }
  .tv-clar-top {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    font-size: 11px; color: var(--ink-3);
  }
  .tv-clar-kind { font-weight: 600; color: var(--ink-2); }
  .tv-clar-bubble.is-open .tv-clar-kind { color: #6d28d9; }
  .tv-clar-body {
    margin: 0; font-size: 13px; line-height: 1.55;
    color: var(--ink); white-space: pre-wrap; word-break: break-word;
  }
  .tv-clar-resolved { font-size: 11px; color: var(--emerald); }
  .tv-clar-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .tv-clar-form { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
  .tv-clar-form-row { display: flex; gap: 8px; align-items: center; justify-content: flex-end; }
  .tv-field-hint { margin: 5px 0 0; font-size: 11.5px; color: var(--ink-3); }

  /* ── Форма задачи с постепенным раскрытием ── */
  .tv-composer { display: flex; flex-direction: column; gap: 2px; }
  .tv-composer-context {
    margin: 0 0 14px; padding: 9px 12px;
    background: #f8f7f4; border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-size: 12.5px; color: var(--ink-2); word-break: break-word;
  }
  .tv-composer-title {
    width: 100%; border: 0; background: none; outline: none;
    padding: 0 2px; font-size: 19px; font-weight: 600;
    color: var(--ink); font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
  }
  .tv-composer-title::placeholder { color: var(--ink-3); font-weight: 500; }
  .tv-composer-description {
    width: 100%; border: 0; background: none; outline: none; resize: vertical;
    padding: 6px 2px 0; min-height: 62px;
    font-size: 13.5px; line-height: 1.55; color: var(--ink-2);
  }
  .tv-composer-description::placeholder { color: var(--ink-3); }
  .tv-composer-assignee {
    display: flex; align-items: center; gap: 10px;
    margin-top: 12px; padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .tv-composer-assignee label {
    font-size: 12px; font-weight: 500; color: var(--ink-2);
    white-space: nowrap;
  }
  .tv-composer-assignee .tv-select, .tv-composer-assignee .tv-person-select { flex: 1; }
  /* Подписи «Проект» и «Исполнитель» одной ширины — поля под ними встают в колонку. */
  .tv-composer-assignee > label { min-width: 74px; }
  /* Строка проекта стоит над исполнителем: разделитель у неё, а не дважды подряд. */
  .tv-composer-project + .tv-composer-assignee { margin-top: 10px; padding-top: 0; border-top: 0; }
  .tv-composer-project-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .tv-composer-project-body .tv-person-select { width: 100%; }
  .tv-composer-project.is-invalid .tv-person-select > button:not([aria-expanded="true"]) {
    border-color: #fda4af; box-shadow: 0 0 0 2px #ffe4e6;
  }
  .tv-composer-row-label { display: inline-flex; align-items: center; gap: 5px; }
  /* Выбранные исполнители в кнопке селектора: стопка лиц + имя первого. */
  .tv-assignee-value { display: inline-flex; align-items: center; gap: 7px; min-width: 0; }
  .tv-assignee-value-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tv-composer-self {
    flex: 0 0 auto;
    padding: 6px 11px; border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface);
    font-size: 12px; font-weight: 500; color: var(--ink-2);
    cursor: pointer; transition: all .15s; white-space: nowrap;
  }
  .tv-composer-self:hover:not(:disabled) { border-color: var(--border-strong); color: var(--ink); }
  .tv-composer-self.is-active { background: var(--ink); border-color: var(--ink); color: #fff; }
  .tv-composer-self:disabled { opacity: .5; cursor: not-allowed; }
  /* Строка исполнителей: поле и «Себе» в линию. У доски со своими исполнителями
     (24.11.2026) над ними — выбор «Все / Конкретные люди», подпись — на уровне
     сегментов, а не посередине двух строк. */
  .tv-composer-assignee-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 7px; }
  .tv-composer-assignee-line { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .tv-composer-assignee-scope { display: flex; align-items: center; gap: 6px; align-self: flex-start; }
  .tv-composer-assignee.has-scope { align-items: flex-start; }
  .tv-composer-assignee.has-scope > label { min-height: 29px; display: inline-flex; align-items: center; }
  .tv-composer-segment-count {
    margin-left: 5px; font-weight: 400; color: var(--ink-3);
    font-variant-numeric: tabular-nums;
  }
  /* «Все»: кому уйдёт задача — не поле выбора, а сводка высотой с селектор. */
  .tv-assignee-all {
    flex: 1; min-width: 0;
    display: flex; align-items: center; gap: 8px;
    min-height: 37px; padding: 0 12px;
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    background: var(--surface-2);
    font-size: 13px; color: var(--ink);
  }

  .tv-composer-chips {
    display: flex; flex-wrap: wrap; gap: 5px;
    margin-top: 12px;
  }
  /* «+ Срочность», «+ Тип»… — серые капсулы без рамки; раскрытое поле — мягкий голубой. */
  .tv-composer-chip {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 5px 11px; border-radius: 99px;
    border: 1px solid transparent;
    background: var(--fill);
    font-size: 12px; font-weight: 500; color: var(--ink-2);
    cursor: pointer; transition: all .15s; white-space: nowrap;
  }
  .tv-composer-chip:hover:not(:disabled) { background: var(--fill-strong); color: var(--ink); }
  .tv-composer-chip:active:not(:disabled) { transform: scale(.97); }
  .tv-composer-chip:disabled { opacity: .5; cursor: not-allowed; }
  .tv-composer-chip.is-active {
    background: var(--select-bg); border-color: var(--select-border); color: var(--select-ink);
  }
  .tv-composer-chip-sign {
    font-size: 13px; line-height: 1; color: var(--ink-3);
    font-weight: 400;
  }
  .tv-composer-chip.is-active .tv-composer-chip-sign { color: var(--select-ink); }
  .tv-composer-chip-value {
    padding-left: 5px; margin-left: 1px;
    border-left: 1px solid var(--border-strong);
    font-weight: 400; color: var(--ink-3);
    font-variant-numeric: tabular-nums;
  }

  .tv-composer-details {
    display: flex; flex-direction: column; gap: 12px;
    margin-top: 14px; padding: 13px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }
  .tv-composer-row { display: flex; align-items: center; gap: 12px; }
  .tv-composer-row.is-stacked { flex-direction: column; align-items: stretch; gap: 7px; }
  .tv-composer-row-label {
    flex: 0 0 96px; font-size: 12px; color: var(--ink-2); font-weight: 500;
  }
  .tv-composer-row.is-stacked .tv-composer-row-label { flex: none; }
  .tv-composer-row-body { flex: 1; display: flex; flex-direction: column; gap: 7px; min-width: 0; }
  .tv-composer-row-inline { flex-direction: row; align-items: center; }
  .tv-composer-row-inline .tv-select { flex: 1; }
  .tv-composer-row-inline .tv-input { width: 78px; }

  .tv-composer-segmented {
    display: inline-flex; gap: 2px; padding: 2px;
    border-radius: 9px; background: #e8e8ed;
  }
  /* В колонке строки (под сегментами — поле или строка ошибки) элемент по
     умолчанию тянется на всю ширину, и серая подложка уходила далеко за
     последнюю кнопку («Тип», «Кто поручил»). Подложка — по кнопкам. */
  .tv-composer-row-body > .tv-composer-segmented { align-self: flex-start; }
  .tv-composer-segmented button {
    border: 0; background: transparent; cursor: pointer;
    padding: 5px 11px; border-radius: 7px;
    font-size: 12px; font-weight: 500; color: var(--ink-2);
    transition: background .15s, color .15s, box-shadow .15s;
    white-space: nowrap;
  }
  .tv-composer-segmented button:hover:not(:disabled) { color: var(--ink); }
  .tv-composer-segmented button.is-active {
    background: var(--surface); color: var(--ink);
    box-shadow: 0 1px 3px rgba(0,0,0,.10);
  }
  .tv-composer-segmented button:disabled { opacity: .55; cursor: not-allowed; }
  /* Сегменты во всю ширину поля — в узких формах (детали заметки, закреплённая задача),
     где выбор из трёх стоит вместо системного списка. */
  .tv-composer-segmented.is-stretch { display: flex; width: 100%; }
  .tv-composer-segmented.is-stretch button {
    flex: 1 1 0; min-width: 0; padding-left: 6px; padding-right: 6px;
    overflow: hidden; text-overflow: ellipsis;
  }
  .tv-composer-presets { display: flex; flex-wrap: wrap; gap: 5px; }
  .tv-composer-presets button {
    border: 1px solid var(--border); background: var(--surface);
    border-radius: 7px; cursor: pointer;
    padding: 4px 9px; font-size: 12px; color: var(--ink-2);
    transition: all .15s; white-space: nowrap;
  }
  .tv-composer-presets button:hover:not(:disabled) { border-color: var(--border-strong); color: var(--ink); }
  .tv-composer-presets button.is-active {
    background: var(--select-bg); border-color: var(--select-border); color: var(--select-ink);
  }
  .tv-composer-presets button:disabled { opacity: .5; cursor: not-allowed; }
  /* Дедлайн (TaskDeadlineField): быстрые сроки над днём и временем. День и время —
     классами полей раздела (.tv-person-select, .tv-input), цвета здесь только
     переменными: в окне «поверх окон» их задаёт палитра панели. */
  .tv-due-field { display: flex; flex-direction: column; gap: 7px; min-width: 0; }
  .tv-due-pair { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px; }
  /* Кнопка дня — ростом с поле времени рядом (на телефоне поля раздела выше кнопок). */
  .tv-due-pair > .tv-person-select > button { height: 100%; }
  .tv-due-field .tv-input.tv-due-time {
    width: 96px; padding-left: 22px; padding-right: 22px;
    text-align: center; font-variant-numeric: tabular-nums;
  }
  /* Срок в прошлом — кайма у дня и времени, как у «Типа» без выбора. */
  .tv-composer-deadline.is-invalid .tv-due-field .tv-person-select > button:not([aria-expanded="true"]),
  .tv-composer-deadline.is-invalid .tv-due-field .tv-input:not(:focus) {
    box-shadow: 0 0 0 2px #fda4af;
  }
  .tv-stat-card {
    background: var(--surface);
    border: 1px solid transparent;
    box-shadow: 0 0 0 1px rgba(15,23,42,.06), 0 1px 2px rgba(15,23,42,.04);
    border-radius: var(--radius);
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    transition: box-shadow .15s, transform .15s;
  }
  .tv-stat-card:hover {
    box-shadow: var(--shadow-md);
    transform: translateY(-1px);
  }
  .tv-stat-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .06em;
    text-transform: uppercase;
    color: var(--ink-3);
  }
  .tv-stat-value {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
    color: var(--ink);
    line-height: 1;
  }
  .tv-stat-card.is-amber .tv-stat-value { color: #b45309; }
  .tv-stat-card.is-indigo .tv-stat-value { color: var(--indigo); }
  .tv-stat-card.is-emerald .tv-stat-value { color: var(--emerald); }

  /* ── Section ── */
  .tv-section { margin-bottom: 36px; }
  .tv-section-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 14px; gap: 12px;
  }
  .tv-section-title {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
    font-size: 13px; font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase;
    color: var(--ink-3);
  }

  /* ── Search & Filters ── */
  .tv-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .tv-search-wrap {
    flex: 1;
    min-width: 180px;
    position: relative;
  }
  .tv-search-icon {
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--ink-3);
    pointer-events: none;
  }
  .tv-search-input {
    width: 100%;
    padding: 8px 12px 8px 32px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-size: 13px;
    color: var(--ink);
    background: var(--surface);
    outline: none;
    transition: border .15s, box-shadow .15s;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
  }
  .tv-search-input:focus {
    border-color: #93c5fd;
    box-shadow: 0 0 0 3px rgba(37,99,235,.15);
  }
  .tv-search-input::placeholder { color: var(--ink-3); }
  .tv-filter-chips {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
  }
  .tv-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 5px 11px;
    border-radius: 99px;
    border: 1px solid var(--border);
    background: var(--surface);
    font-size: 12px;
    font-weight: 500;
    color: var(--ink-2);
    cursor: pointer;
    transition: all .15s;
    white-space: nowrap;
  }
  .tv-chip:hover { border-color: var(--border-strong); color: var(--ink); }
  .tv-chip:active { transform: scale(.97); }
  /* Выбранный фильтр — мягкий голубой, как выбор везде на сайте (не чёрная заливка). */
  .tv-chip.is-active {
    background: var(--select-bg);
    border-color: var(--select-border);
    color: var(--select-ink);
  }
  .tv-chip.is-active-amber  { background: #fef3c7; border-color: #fde68a; color: #92400e; }
  .tv-chip.is-active-rose   { background: #ffe4e6; border-color: #fecdd3; color: #9f1239; }
  .tv-chip.is-active-indigo { background: #eef2ff; border-color: #c7d2fe; color: #3730a3; }
  .tv-chip.is-active-emerald{ background: #d1fae5; border-color: #a7f3d0; color: #065f46; }
  .tv-chip.is-active-violet { background: #ede9fe; border-color: #ddd6fe; color: #5b21b6; }

  /* ── Buttons ── */
  .tv-btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 8px 14px; border-radius: var(--radius-sm);
    font-size: 13px; font-weight: 600;
    border: 1px solid transparent;
    cursor: pointer; transition: all .15s ease;
    white-space: nowrap; background: none;
  }
  .tv-btn:disabled { opacity: .5; cursor: not-allowed; }
  .tv-btn:active:not(:disabled) { transform: scale(.98); }
  /* Вторичная кнопка — серая заливка без рамки, как iosBtnSecondary на сайте. */
  .tv-btn-ghost {
    background: var(--fill);
    border-color: transparent;
    color: var(--ink-2);
  }
  .tv-btn-ghost:hover:not(:disabled) {
    background: var(--fill-strong); color: var(--ink);
  }
  /* В шапке раздела кнопки лежат на сером полотне — там они белые, с тонкой каймой. */
  .tv-topbar-actions .tv-btn-ghost:not(.is-hot) {
    background: var(--surface);
    box-shadow: 0 0 0 1px rgba(15,23,42,.07), 0 1px 2px rgba(15,23,42,.04);
  }
  .tv-topbar-actions .tv-btn-ghost:not(.is-hot):hover:not(:disabled) { background: var(--surface-2); }
  .tv-btn-primary { background: var(--accent); color: var(--accent-fg); border-color: var(--accent); }
  .tv-btn-primary:hover:not(:disabled) { background: #1d4ed8; border-color: #1d4ed8; }
  .tv-btn-amber   { background: #fef3c7; color: #b45309; border-color: transparent; }
  .tv-btn-amber:hover:not(:disabled)   { background: #fde68a; }
  .tv-btn-indigo  { background: #eef2ff; color: var(--indigo); border-color: transparent; }
  .tv-btn-indigo:hover:not(:disabled)  { background: #e0e7ff; }
  .tv-btn-emerald { background: #d1fae5; color: #047857; border-color: transparent; }
  .tv-btn-emerald:hover:not(:disabled) { background: #a7f3d0; }
  .tv-btn-rose    { background: #ffe4e6; color: var(--rose); border-color: transparent; }
  .tv-btn-rose:hover:not(:disabled)    { background: #fecdd3; }

  /* ── Task Row ── */
  .tv-task-list { display: flex; flex-direction: column; gap: 3px; }
  .tv-task-date-separator {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: 8px;
    margin: 9px 2px 5px 10px;
    color: var(--ink-3);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .04em;
    text-transform: uppercase;
  }
  .tv-task-date-separator::before {
    content: none;
  }
  .tv-task-date-separator::after {
    content: '';
    flex: 1;
    min-width: 14px;
    height: 1px;
    background: var(--border);
  }
  .tv-task-row {
    display: flex; align-items: center; gap: 10px;
    padding: 11px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: all .15s ease;
    position: relative;
  }
  .tv-task-row:hover {
    border-color: var(--border-strong);
    box-shadow: var(--shadow-sm);
    transform: translateY(-1px);
  }
  /* Срочность строку целиком больше не красит (16.11): в сгруппированном списке
     жёлтые и розовые полосы читались бы отдельными карточками — она капсулой справа. */
  .tv-task-row-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .tv-task-row-subject {
    font-size: 13.5px; font-weight: 500; color: var(--ink);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
  }
  .tv-task-row-flow {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 11.5px;
    color: var(--ink-3);
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tv-task-row-flow-name {
    min-width: 0;
    max-width: 120px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tv-task-row-flow-arrow {
    flex-shrink: 0;
    color: var(--ink-2);
    font-weight: 700;
  }
  .tv-task-row-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
    padding-left: 8px;
  }
  .tv-task-row-meta > * { flex-shrink: 0; }
  .tv-task-row-assignee-chip {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 12px; color: var(--ink-2);
    min-width: 0;
    max-width: 150px;
    overflow: hidden;
    white-space: nowrap;
  }
  .tv-task-row-assignee-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tv-avatar-xs {
    width: 20px; height: 20px; border-radius: 50%;
    background: #e2e8f0;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 9px; font-weight: 700;
    color: var(--ink-2);
    flex-shrink: 0;
    text-transform: uppercase;
  }
  .tv-avatar-media {
    width: 100%;
    height: 100%;
    border-radius: 50%;
    object-fit: cover;
    display: block;
  }
  /* Стопка аватаров: несколько исполнителей в ширину одного чипа. Обводка
     цветом фона нужна, чтобы наложенные лица не слипались в кашу. */
  .tv-avatar-stack { display: inline-flex; align-items: center; flex-shrink: 0; }
  .tv-avatar-stacked { box-shadow: 0 0 0 2px var(--surface); }
  .tv-avatar-stacked + .tv-avatar-stacked { margin-left: -6px; }
  .tv-avatar-rest {
    background: #dcd8d0;
    font-size: 8.5px;
    letter-spacing: -.2px;
  }
  .tv-task-row-assignee-chip.is-stack { max-width: none; }
  .tv-task-row-date {
    font-size: 11.5px;
    color: var(--ink-3);
    white-space: nowrap;
    text-align: right;
    min-width: 92px;
  }
  .tv-row-pin-btn {
    width: 28px;
    height: 28px;
    border-radius: 8px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--ink-3);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all .15s ease;
  }
  .tv-row-pin-btn:hover,
  .tv-row-pin-btn.is-pinned {
    color: var(--indigo);
    background: #eef2ff;
    border-color: #c7d2fe;
  }
  .tv-task-count {
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 20px; height: 18px; padding: 0 5px;
    border-radius: 99px;
    font-size: 10.5px; font-weight: 700;
    background: #f0f0ed; color: var(--ink-2);
    border: 1px solid var(--border);
  }
  .tv-task-count.is-alert { background: #fef3c7; color: #92400e; border-color: #fde68a; }

  /* ── My tasks tabs ── */
  .tv-my-tabs {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-bottom: 14px;
    padding: 3px;
    border: 1px solid var(--border);
    border-radius: 11px;
    background: #f1f5f9;
  }
  .tv-tab-btn {
    border: 0;
    background: transparent;
    color: var(--ink-2);
    font-size: 13px;
    font-weight: 600;
    padding: 7px 14px;
    border-radius: 9px;
    cursor: pointer;
    transition: all .15s ease;
    display: inline-flex; align-items: center; gap: 7px;
  }
  .tv-tab-btn:hover { color: var(--ink); }
  .tv-tab-btn.is-active {
    background: var(--surface);
    color: var(--ink);
    box-shadow: var(--shadow-sm);
  }

  /* ── Person list ── */
  .tv-person-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
  .tv-person-row {
    width: 100%;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    padding: 11px 14px;
    display: flex;
    align-items: center;
    gap: 12px;
    cursor: pointer;
    transition: all .15s ease;
    text-align: left;
  }
  .tv-person-row:hover {
    border-color: var(--border-strong);
    box-shadow: var(--shadow-sm);
    transform: translateY(-1px);
  }
  .tv-person-row.is-active {
    border-color: #c7d2fe;
    background: #eef2ff;
  }
  .tv-avatar-md {
    width: 34px; height: 34px; border-radius: 50%;
    background: #e2e8f0;
    display: flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 700;
    color: var(--ink-2);
    flex-shrink: 0;
    text-transform: uppercase;
  }
  .tv-person-row.is-active .tv-avatar-md { background: #c7d2fe; color: #3730a3; }
  .tv-person-info { flex: 1; min-width: 0; }
  .tv-person-name {
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tv-person-stats {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    font-size: 11.5px;
    color: var(--ink-2);
    margin-top: 3px;
  }
  .tv-person-stat-item { display: flex; align-items: center; gap: 4px; }
  .tv-alert-stat {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: #b45309;
    font-weight: 600;
  }
  .tv-pulse-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: #f59e0b;
    box-shadow: 0 0 0 0 rgba(245, 158, 11, .55);
    animation: tvPulse 1.4s ease-out infinite;
    flex-shrink: 0;
  }
  @keyframes tvPulse {
    0%   { box-shadow: 0 0 0 0 rgba(245, 158, 11, .55); }
    70%  { box-shadow: 0 0 0 8px rgba(245, 158, 11, 0); }
    100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
  }
  .tv-person-tasks-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 10px;
  }
  .tv-person-tasks-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--ink-3);
    letter-spacing: .04em;
    text-transform: uppercase;
  }
  .tv-person-tasks-back {
    font-size: 12px;
    color: var(--indigo);
    background: none;
    border: none;
    cursor: pointer;
    font-weight: 500;
    padding: 0;
    display: flex; align-items: center; gap: 4px;
  }
  .tv-person-tasks-back:hover { text-decoration: underline; }

  /* ── Badges ── */
  /* Метки — цветные капсулы без обводки, как теги в iOS. */
  .tv-badge {
    display: inline-flex; align-items: center;
    padding: 2px 8px; border-radius: 99px;
    font-size: 11px; font-weight: 600;
    border: 1px solid transparent;
    white-space: nowrap; line-height: 1.25;
  }
  .tv-badge-gray    { background: var(--fill); color: var(--ink-2); border-color: transparent; }
  .tv-badge-blue    { background: #dbeafe; color: #1e40af;       border-color: transparent; }
  .tv-badge-amber   { background: #fef3c7; color: #92400e;       border-color: transparent; }
  .tv-badge-indigo  { background: #e0e7ff; color: #3730a3;       border-color: transparent; }
  .tv-badge-emerald { background: #d1fae5; color: #065f46;       border-color: transparent; }
  .tv-badge-rose    { background: #ffe4e6; color: #9f1239;       border-color: transparent; }
  .tv-badge-teal    { background: #ccfbf1; color: #0f766e;       border-color: transparent; }
  .tv-badge-violet  { background: #ede9fe; color: #5b21b6;       border-color: transparent; }
  /* Продакшн (задача #354). Без предка .tv-root, как у остальных бейджей: окно
     закреплённой задачи живёт вне раздела, и бейдж там остался бы бесцветным. */
  .tv-badge-fuchsia { background: #fae8ff; color: #86198f;       border-color: #f5d0fe; }

  /* ── Skeleton loading ── */
  .tv-skeleton {
    background: linear-gradient(90deg, #eee 25%, #f8f8f8 50%, #eee 75%);
    background-size: 200% 100%;
    animation: tvSkeleton 1.4s ease-in-out infinite;
    border-radius: var(--radius-xs);
  }
  @keyframes tvSkeleton {
    0%   { background-position: 200% 0; }
    100% { background-position: -200% 0; }
  }
  .tv-skeleton-row {
    height: 48px;
    border-radius: var(--radius-sm);
    margin-bottom: 3px;
  }

  /* ── Empty ── */
  .tv-empty {
    padding: 40px 24px;
    text-align: center;
    color: var(--ink-3);
    font-size: 13px;
    background: var(--surface);
    border: 1.5px dashed var(--border);
    border-radius: var(--radius);
    display: flex; flex-direction: column; align-items: center; gap: 8px;
  }
  .tv-empty-icon { font-size: 28px; opacity: .5; }
  .tv-empty-title { font-weight: 600; color: var(--ink-2); font-size: 14px; }
  .tv-empty-sub { font-size: 12.5px; }

  /* ── Слои раздела ──
     90  — окно статуса (просмотр колонки целиком)
     110 — затемнение карточки задачи
     111 — карточка задачи
     120 — модалки задачи
     130 — закреплённая задача
     Всё это выше сайдбара приложения (z-50) и гамбургера (60): окно статуса не
     должно уезжать под навигацию, а карточка задачи — под окно.
     Исключение: окно статуса стоит рядом с сайдбаром, не поверх него, поэтому на
     время показа сайдбар поднимается до 100 (см. body.sheet-beside-sidebar в
     src/styles.css) — выше окна, но всё ещё ниже карточки задачи. */

  /* ── Drawer ── */
  .tv-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.3);
    backdrop-filter: blur(2px); z-index: 110;
    animation: tvFadeIn .2s ease;
  }
  @keyframes tvFadeIn { from { opacity: 0 } to { opacity: 1 } }

  .tv-drawer {
    position: fixed; top: 0; right: 0; bottom: 0;
    width: min(560px, 100vw);
    background: var(--surface); z-index: 111;
    display: flex; flex-direction: column;
    box-shadow: var(--shadow-lg);
    animation: tvSlideIn .22s cubic-bezier(.22,1,.36,1);
    overflow: hidden;
  }
  @keyframes tvSlideIn { from { transform: translateX(100%) } to { transform: translateX(0) } }

  .tv-drawer-header {
    padding: 20px 20px 16px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: flex-start; gap: 12px; flex-shrink: 0;
    background: var(--surface-2);
  }
  .tv-drawer-header-text { flex: 1; min-width: 0; }
  .tv-drawer-title {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
    font-size: 16px; font-weight: 600; color: var(--ink);
    margin: 0 0 8px; word-break: break-word; line-height: 1.35;
  }
  .tv-drawer-badges { display: flex; flex-wrap: wrap; gap: 5px; }

  .tv-drawer-header-actions {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    margin-top: 1px;
  }
  .tv-close-btn, .tv-icon-btn {
    width: 32px; height: 32px; border-radius: 8px; flex-shrink: 0;
    background: var(--bg); border: 1px solid var(--border); cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    color: var(--ink-2); transition: all .15s;
  }
  .tv-close-btn:hover, .tv-icon-btn:hover:not(:disabled) {
    background: var(--border-strong); color: var(--ink); border-color: var(--border-strong);
  }
  .tv-icon-btn:disabled {
    opacity: .55;
    cursor: not-allowed;
  }
  .tv-icon-btn-danger {
    color: var(--rose);
    border-color: #fecdd3;
    background: #fff1f2;
  }
  .tv-icon-btn-danger:hover:not(:disabled) {
    background: #ffe4e6;
    border-color: #fecdd3;
    color: #be123c;
  }
  .tv-icon-btn.is-pinned {
    color: var(--indigo);
    border-color: #c7d2fe;
    background: #eef2ff;
  }

  .tv-drawer-body {
    flex: 1; overflow-y: auto; padding: 20px;
    display: flex; flex-direction: column; gap: 18px;
  }
  /* Подвалы окон — белые, как само окно: серые кнопки на сером подвале терялись. */
  .tv-drawer-footer {
    padding: 14px 20px; border-top: 1px solid var(--border);
    display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end;
    flex-shrink: 0; background: var(--surface);
  }

  /* ── Detail blocks ── */
  .tv-info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .tv-info-item label {
    display: block; font-size: 10.5px; font-weight: 600;
    letter-spacing: .06em; text-transform: uppercase;
    color: var(--ink-3); margin-bottom: 3px;
  }
  .tv-info-item span { font-size: 13px; color: var(--ink); }
  /* Балл в карточке: подпись с «i» в строку, а сегменты живут своими размерами
     и цветами — правило выше перекрасило бы их цифры в одинаково тёмные. */
  .tv-info-item .tv-info-label-row { display: flex; align-items: center; gap: 4px; }
  .tv-info-item .tv-score-control span { font-size: inherit; color: inherit; }
  .tv-info-item .tv-score-value { display: inline-flex; align-items: center; gap: 6px; }
  .tv-divider { height: 1px; background: var(--border); margin: 0; }
  .tv-description {
    font-size: 13.5px; color: var(--ink-2); line-height: 1.65;
    white-space: pre-wrap; word-break: break-word;
  }
  .tv-block-label {
    font-size: 10.5px; font-weight: 600; letter-spacing: .06em;
    text-transform: uppercase; color: var(--ink-3); margin-bottom: 8px;
  }
  .tv-file-list { display: flex; flex-wrap: wrap; gap: 6px; }
  .tv-file-btn {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 6px 11px; border-radius: var(--radius-sm);
    background: #f1f5f9; border: 1px solid var(--border);
    color: var(--ink-2); font-size: 12px;
    cursor: pointer; transition: all .12s;
    max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .tv-file-btn:hover { background: var(--border); color: var(--ink); }
  /* Длинное имя — с многоточием, значок файла не сжимается до нуля: в кнопке-flex
     текст не обрезается сам, а svg уступал ему всё место. Полное имя — в подсказке
     и в шапке окна просмотра. */
  .tv-file-btn > svg { flex: none; }
  .tv-file-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* Картинки во вложениях — квадратные плитки с обрезкой по центру, как в
     «Сообщениях». Пока адрес не пришёл, плитка стоит пустой: место занято, и
     кнопки файлов ниже не прыгают. Без мерцания-«скелета» — оно было бы шумом
     на долю секунды. Цвета — переменные раздела, поэтому тёмная тема своя. */
  .tv-photo-grid { display: flex; flex-wrap: wrap; gap: 6px; }
  .tv-photo-grid + .tv-file-list { margin-top: 8px; }
  .tv-photo-tile {
    display: block; flex: none; width: 76px; height: 76px; padding: 0;
    border-radius: 12px; overflow: hidden;
    border: 1px solid var(--border); background: var(--surface-2);
    cursor: zoom-in; transition: transform .12s ease, border-color .12s ease;
  }
  .tv-photo-tile img {
    display: block; width: 100%; height: 100%; object-fit: cover;
    -webkit-user-drag: none; user-select: none;
  }
  .tv-photo-tile:hover:not(:disabled) { border-color: var(--border-strong); }
  .tv-photo-tile:active:not(:disabled) { transform: scale(.97); }
  .tv-photo-tile:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
  .tv-photo-tile:disabled { cursor: default; }
  .tv-clar-bubble .tv-photo-tile { width: 64px; height: 64px; border-radius: 10px; }

  .tv-deadline-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 11.5px;
    font-weight: 600;
    color: var(--ink-2);
    white-space: nowrap;
  }
  .tv-deadline-chip.is-overdue { color: var(--rose); }
  .tv-soft-block {
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface-2);
    padding: 12px;
  }
  .tv-checklist { display: flex; flex-direction: column; gap: 7px; }
  .tv-checklist-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 8px 9px;
    border: 1px solid var(--border);
    border-radius: 9px;
    background: var(--surface);
  }
  .tv-checklist-row.is-done {
    background: #f0fdf4;
    border-color: #bbf7d0;
  }
  .tv-checklist-checkbox {
    margin-top: 2px;
    width: 16px;
    height: 16px;
    accent-color: var(--emerald);
    flex-shrink: 0;
  }
  .tv-checklist-title {
    flex: 1;
    min-width: 0;
    color: var(--ink);
    font-size: 13px;
    line-height: 1.4;
    word-break: break-word;
  }
  .tv-checklist-row.is-done .tv-checklist-title {
    color: var(--ink-3);
    text-decoration: line-through;
  }
  .tv-checklist > .tv-checklist-row {
    flex-direction: column;
    align-items: stretch;
    gap: 7px;
  }
  .tv-checklist-line {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    cursor: pointer;
  }
  .tv-checklist-result {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding-left: 24px;
  }
  .tv-checklist-result-input {
    width: 100%;
    resize: vertical;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--surface);
    padding: 6px 8px;
    font-size: 12.5px;
    line-height: 1.4;
    color: var(--ink);
    font-family: inherit;
    outline: none;
    transition: border .15s, box-shadow .15s;
  }
  .tv-checklist-result-input:focus {
    border-color: var(--emerald);
    box-shadow: 0 0 0 3px rgba(5,150,105,.10);
  }
  .tv-checklist-result-input::placeholder { color: var(--ink-3); }
  .tv-checklist-result-input:disabled { opacity: .6; cursor: not-allowed; }
  .tv-checklist-result-meta {
    font-size: 11px;
    color: var(--ink-3);
  }
  .tv-note-textarea {
    min-height: 112px;
    background: #fffdf5;
    border-color: #fde68a;
  }
  .tv-notes-panel {
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-height: 0;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    padding: 12px;
    box-shadow: var(--shadow-sm);
  }
  .tv-notes-panel.is-compact {
    padding: 10px;
    gap: 9px;
    max-height: min(620px, calc(100vh - 112px));
    overflow: hidden;
  }
  .tv-notes-panel.is-fullscreen {
    flex: 1;
    min-height: 0;
    height: 100%;
    overflow: hidden;
    box-shadow: none;
  }
  /* Широкое окно: рамку и тень даёт само окно, панель их не дублирует. */
  .tv-notes-panel.is-wide {
    border: 0;
    background: transparent;
    box-shadow: none;
    padding: 0;
    gap: 14px;
  }
  .tv-notes-panel.is-wide .tv-notes-toolbar { justify-content: flex-end; }
  /* Список слева тянется, редактор справа фиксированной удобной ширины. */
  .tv-notes-panel.is-wide .tv-notes-content {
    grid-template-columns: minmax(0, 1fr) minmax(340px, 420px);
    gap: 16px;
    align-items: start;
    overflow: visible;
  }
  .tv-notes-panel.is-wide .tv-note-column-list { max-height: none; }
  .tv-notes-panel.is-wide .tv-notes-editor {
    position: sticky;
    top: 0;
  }
  .tv-notes-panel.is-wide .tv-note-textarea { min-height: 260px; }
  /* На широких экранах помещается третья колонка — доска и редактор дышат. */
  @media (min-width: 1500px) {
    .tv-notes-panel.is-wide .tv-notes-content {
      grid-template-columns: minmax(0, 1fr) minmax(380px, 460px);
    }
  }
  @media (max-width: 900px) {
    .tv-notes-panel.is-wide .tv-notes-content { grid-template-columns: minmax(0, 1fr); }
    .tv-notes-panel.is-wide .tv-notes-editor { position: static; }
  }
  .tv-notes-toolbar,
  .tv-notes-toolbar-actions,
  .tv-notes-list-head,
  .tv-notes-editor-footer,
  .tv-notes-editor-actions,
  .tv-note-card-head,
  .tv-note-card-meta,
  .tv-note-switches,
  .tv-notes-editor-head,
  .tv-note-due-pill {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .tv-notes-toolbar,
  .tv-notes-editor-footer {
    justify-content: space-between;
  }
  .tv-notes-toolbar p {
    margin: 2px 0 0;
    color: var(--ink-3);
    font-size: 11px;
  }
  .tv-notes-content {
    display: grid;
    grid-template-columns: minmax(0, 1.15fr) minmax(260px, .85fr);
    gap: 12px;
    min-height: 0;
    overflow: hidden;
  }
  .tv-notes-panel.is-compact .tv-notes-content {
    max-height: min(500px, calc(100vh - 200px));
    overflow-y: auto;
    padding-right: 2px;
  }
  .tv-notes-panel.is-fullscreen .tv-notes-content {
    flex: 1;
    overflow-y: auto;
    padding-right: 2px;
  }
  .tv-notes-board {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    min-width: 0;
  }
  .tv-note-column {
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface-2);
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .tv-note-column-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
  }
  .tv-note-column-head strong {
    display: block;
    font-size: 12.5px;
    color: var(--ink);
  }
  .tv-note-column-head small {
    display: block;
    margin-top: 2px;
    font-size: 10.5px;
    color: var(--ink-3);
  }
  .tv-note-column-count {
    min-width: 24px;
    height: 22px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0 7px;
    background: var(--surface);
    color: var(--ink-2);
    border: 1px solid var(--border);
    font-size: 11px;
    font-weight: 800;
  }
  /* Подпись пустой колонки заметок. Жила под классом старой закреплённой задачи
     (.tv-pin-empty-actions) и пропала вместе с ним — теперь своя, у колонок. */
  .tv-note-column-empty {
    color: var(--ink-3);
    font-size: 12px;
    line-height: 1.45;
  }
  .tv-note-column-list {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    overflow-y: auto;
    max-height: 320px;
    padding-right: 2px;
  }
  .tv-note-topic-btn {
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    color: var(--ink);
    padding: 9px;
    cursor: pointer;
    text-align: left;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
    transition: border .15s, background .15s, transform .15s, box-shadow .15s;
  }
  .tv-note-topic-btn:hover,
  .tv-note-topic-btn.is-active {
    border-color: var(--accent);
    background: var(--bg);
  }
  .tv-note-topic-btn:hover { transform: translateY(-1px); }
  .tv-note-topic-btn.is-active { box-shadow: 0 0 0 3px rgba(37,99,235,.12); }
  .tv-note-topic-btn.is-done {
    border-color: #a7f3d0;
    background: #ecfdf5;
  }
  .tv-note-topic-btn.is-done .tv-note-topic-title {
    color: #047857;
    text-decoration: line-through;
  }
  .tv-note-topic-btn.is-done .tv-note-topic-date { color: #059669; }
  .tv-note-card-head { min-width: 0; width: 100%; }
  .tv-note-topic-title {
    font-size: 12.5px;
    font-weight: 800;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }
  .tv-note-topic-date {
    color: var(--ink-3);
    font-size: 10.5px;
  }
  .tv-note-reminder-mark {
    display: inline-flex; align-items: center;
    margin-left: 5px; color: var(--ink-3);
    vertical-align: -1px;
  }
  .tv-note-card-meta {
    flex-wrap: wrap;
    gap: 5px;
  }
  .tv-note-task-mark,
  .tv-note-due-pill {
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: var(--ink-2);
    border-radius: 999px;
    padding: 2px 7px;
    font-size: 10.5px;
    font-weight: 800;
  }
  .tv-note-due-pill {
    background: #fffbeb;
    border-color: #fde68a;
    color: #92400e;
    max-width: 100%;
  }
  .tv-note-done-toggle {
    width: 20px;
    height: 20px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--emerald);
    background: #ecfdf5;
    border: 1px solid #a7f3d0;
    flex: 0 0 auto;
  }
  .tv-notes-editor {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    padding: 12px;
    box-shadow: var(--shadow-sm);
  }
  .tv-notes-editor.is-done {
    border-color: #a7f3d0;
    background: #f0fdf4;
  }
  .tv-notes-editor .tv-note-textarea {
    flex: 1;
    min-height: 150px;
  }
  .tv-notes-editor-head {
    justify-content: space-between;
    min-width: 0;
  }
  .tv-note-controls-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(130px, .5fr);
    gap: 8px;
  }
  /* Сегменты приоритета — на всю ширину: в левой колонке «Критичная» не помещалась. */
  .tv-note-controls-grid > .is-wide { grid-column: 1 / -1; }
  .tv-note-advanced-toggle {
    width: 100%;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-2);
    color: var(--ink-2);
    padding: 7px 9px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 800;
    transition: background .15s, border .15s, color .15s;
  }
  .tv-note-advanced-toggle:hover:not(:disabled),
  .tv-note-advanced-toggle.is-open {
    background: var(--bg);
    border-color: var(--border-strong);
    color: var(--ink);
  }
  .tv-note-advanced-toggle:disabled {
    opacity: .65;
    cursor: not-allowed;
  }
  .tv-note-advanced-toggle-main,
  .tv-note-advanced-toggle-meta {
    min-width: 0;
    display: inline-flex;
    align-items: center;
    gap: 7px;
  }
  .tv-note-advanced-toggle-meta {
    color: var(--ink-3);
    font-size: 11px;
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tv-note-advanced-caret {
    transition: transform .15s ease;
    flex: 0 0 auto;
  }
  .tv-note-advanced-toggle.is-open .tv-note-advanced-caret {
    transform: rotate(180deg);
  }
  .tv-note-advanced-panel {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface-2);
  }
  .tv-notes-editor-status {
    min-width: 0;
    color: var(--ink-3);
    font-size: 11px;
    line-height: 1.35;
  }
  .tv-note-switches {
    flex-wrap: wrap;
    justify-content: flex-start;
  }
  .tv-pin-widget.is-detached .tv-notes-editor .tv-note-textarea {
    min-height: 0;
  }
  .tv-notes-panel.is-compact .tv-notes-content,
  .tv-notes-panel.is-fullscreen .tv-notes-content {
    grid-template-columns: 1fr;
  }
  .tv-notes-panel.is-compact .tv-notes-board,
  .tv-notes-panel.is-fullscreen .tv-notes-board {
    grid-template-columns: 1fr 1fr;
  }
  .tv-notes-panel.is-compact .tv-note-column-list,
  .tv-notes-panel.is-fullscreen .tv-note-column-list {
    max-height: min(210px, 34vh);
  }
  .tv-notes-panel.is-fullscreen .tv-notes-editor {
    flex: 1;
    min-height: 0;
  }
  .tv-notes-panel.is-fullscreen .tv-notes-editor .tv-note-textarea {
    min-height: 96px;
  }
  .tv-form-switch {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
    color: var(--ink-2);
  }
  .tv-form-switch input { accent-color: var(--ink); }
  .tv-checklist-editor {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .tv-checklist-editor-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 34px;
    gap: 8px;
    align-items: center;
  }
  .tv-checklist-required {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--ink-2);
    font-size: 12px;
    white-space: nowrap;
  }
  .tv-checklist-required input { accent-color: var(--emerald); }
  .tv-checklist-editor-remove {
    width: 34px;
    height: 34px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    color: var(--rose);
    cursor: pointer;
  }
  .tv-checklist-editor-remove:hover:not(:disabled) {
    background: #fff1f2;
    border-color: #fecdd3;
  }

  .tv-completion-block {
    background: #f0f4ff; border: 1px solid #c7d2fe;
    border-radius: var(--radius-sm); padding: 14px;
  }
  .tv-completion-block .tv-block-label { color: var(--indigo); }

  .tv-history-list { display: flex; flex-direction: column; gap: 8px; padding: 4px 0; }
  .tv-history-item {
    display: flex;
    width: 100%;
  }
  .tv-history-bubble {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 8px 12px;
    border-radius: 12px;
    font-size: 12.5px;
    max-width: 85%;
    word-break: break-word;
  }
  .tv-history-item-sender {
    justify-content: flex-start;
  }
  .tv-history-item-sender .tv-history-bubble {
    background: #eef2ff;
    border: 1px solid #c7d2fe;
    border-radius: 4px 12px 12px 12px;
  }
  .tv-history-item-recipient {
    justify-content: flex-end;
  }
  .tv-history-item-recipient .tv-history-bubble {
    background: #d1fae5;
    border: 1px solid #a7f3d0;
    border-radius: 12px 4px 12px 12px;
  }
  .tv-history-item-neutral {
    justify-content: center;
  }
  .tv-history-item-neutral .tv-history-bubble {
    background: #f5f5f7;
    border: 1px solid var(--border);
    border-radius: 12px;
    text-align: center;
    font-size: 11.5px;
    color: var(--ink-2);
    padding: 5px 10px;
  }
  .tv-history-status { font-weight: 600; color: var(--ink); }
  .tv-history-time   { color: var(--ink-3); font-size: 11px; }
  .tv-history-who    { color: var(--ink-2); font-style: italic; font-size: 11px; }
  .tv-history-comment{ color: var(--ink-2); margin-top: 2px; white-space: pre-wrap; }

  /* ── Participants ── */
  .tv-participants {
    display: flex; gap: 16px; flex-wrap: wrap;
  }
  /* Исполнителей может быть несколько, поэтому карточки участников переносятся,
     а не сжимаются до нечитаемых огрызков. */
  .tv-participant {
    display: flex; align-items: center; gap: 8px; flex: 1 1 180px; min-width: 0;
  }
  .tv-participant-avatar {
    width: 32px; height: 32px; border-radius: 50%;
    background: #e2e8f0;
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; font-weight: 700; color: var(--ink-2);
    flex-shrink: 0; text-transform: uppercase;
  }
  .tv-participant-info { min-width: 0; display: flex; flex-direction: column; }
  .tv-participant-add {
    border: 0; background: none; padding: 0; margin: 0; cursor: pointer; text-align: left;
    font: inherit; color: inherit; border-radius: 12px; transition: transform .12s ease;
  }
  .tv-participant-add:active { transform: scale(.97); }
  .tv-participant-add .tv-participant-avatar {
    background: transparent; border: 1.5px dashed #c9c5bd; color: var(--ink-3);
  }
  .tv-participant-add:hover .tv-participant-avatar { border-color: #2563eb; color: #2563eb; }
  .tv-participant-add .tv-participant-name { color: #2563eb; }
  .tv-participant-role { font-size: 10px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
  .tv-participant-name { font-size: 13px; font-weight: 500; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* ── Modal ── */
  .tv-modal-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.35);
    backdrop-filter: blur(3px); z-index: 120;
    display: flex; align-items: center; justify-content: center;
    padding: 16px; animation: tvFadeIn .18s ease;
  }
  .tv-modal {
    background: var(--surface); width: 100%; max-width: 560px;
    border-radius: var(--radius); box-shadow: var(--shadow-lg);
    overflow: hidden; animation: tvScaleIn .2s cubic-bezier(.22,1,.36,1);
    max-height: 90vh; display: flex; flex-direction: column;
  }
  @keyframes tvScaleIn { from { transform: scale(.95); opacity: 0 } to { transform: scale(1); opacity: 1 } }
  .tv-modal-header {
    padding: 18px 22px 16px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    flex-shrink: 0; background: var(--surface-2);
  }
  .tv-modal-title {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
    font-size: 15px; font-weight: 600; color: var(--ink); margin: 0;
  }
  .tv-modal-body { padding: 20px 22px; overflow-y: auto; flex: 1; }
  /* Форма-обёртка между окном и телом (новая задача, правка, сдача, возврат).
     Без этого правила она росла по содержимому: окно упиралось в 90vh и
     обрезало всё ниже, тело не получало предела высоты и не прокручивалось,
     а подвал с «Поставить задачу» уходил за край — задача #361, длинный
     чек-лист. Рост пропускаем сквозь форму, как на телефоне (mobile-shell.css). */
  .tv-modal > form {
    display: flex; flex-direction: column;
    flex: 1 1 auto; min-height: 0;
  }
  .tv-modal-footer {
    padding: 13px 22px; border-top: 1px solid var(--border);
    display: flex; justify-content: flex-end; gap: 8px;
    background: var(--surface); flex-shrink: 0;
  }
  /* Окна, в которые пишут задачу («Новая задача» и правка), на компьютере
     шире и выше остальных: в общих 560 пикселях описание вставало узкой
     колонкой высотой в четыре строки, и длинную задачу набирали вслепую.
     Короткие окна (удаление, возврат, завершение) намеренно остаются прежними.
     Правило под медиазапросом — на телефоне окно и так во весь экран,
     и его размеры задаёт mobile-shell.css. */
  @media (min-width: 900px) {
    .tv-modal.tv-modal-composer { max-width: 720px; }
    .tv-modal.tv-modal-composer .tv-composer-description { min-height: 150px; }
  }
  @media (min-width: 1500px) {
    .tv-modal.tv-modal-composer { max-width: 820px; }
    .tv-modal.tv-modal-composer .tv-composer-description { min-height: 200px; }
  }

  /* ── Form ── */
  .tv-form-grid { display: flex; flex-direction: column; gap: 14px; }
  .tv-form-field label {
    display: block; font-size: 11.5px; font-weight: 500;
    color: var(--ink-2); margin-bottom: 5px;
  }
  .tv-input, .tv-textarea, .tv-select {
    width: 100%; padding: 9px 12px;
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    font-size: 13px; color: var(--ink); background: var(--bg);
    outline: none; transition: border .15s, box-shadow .15s;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
  }
  .tv-input:focus, .tv-textarea:focus, .tv-select:focus {
    border-color: #93c5fd; box-shadow: 0 0 0 3px rgba(37,99,235,.15); background: var(--surface);
  }
  /* Закрытый список — с тем же шевроном, что у списков сайта, а не стрелкой ОС. */
  .tv-select {
    appearance: none; -webkit-appearance: none;
    padding-right: 32px;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    cursor: pointer;
  }
  .tv-input:disabled, .tv-textarea:disabled, .tv-select:disabled { opacity: .55; cursor: not-allowed; }
  .tv-textarea { resize: vertical; min-height: 86px; line-height: 1.5; }

  /* Выбор человека — общий селектор сайта с поиском (components/ui/CustomSelect).
     Кнопку подгоняем под поля раздела, чтобы в одной форме не стояли рядом два
     разных по виду поля; сам список остаётся в дизайн-системе сайта, как и у
     переключателя отделов выше. */
  .tv-person-select { width: 100%; min-width: 0; }
  .tv-person-select > button {
    padding: 9px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg);
    box-shadow: none;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    font-size: 13px; font-weight: 400; color: var(--ink);
    transition: border .15s, box-shadow .15s, transform .12s;
  }
  .tv-person-select > button:hover:not(:disabled) { border-color: var(--border-strong); }
  .tv-person-select > button[aria-expanded="true"] {
    border-color: #93c5fd; box-shadow: 0 0 0 3px rgba(37,99,235,.15);
  }
  /* Системную обводку браузера убираем: после выбора список возвращает фокус на
     кнопку. Фокус показываем так же, как у остальных полей раздела, — мягким
     синим кольцом, как в iOS. */
  .tv-person-select > button:focus,
  .tv-person-select > button:focus-visible {
    outline: none;
    border-color: #93c5fd; box-shadow: 0 0 0 3px rgba(37,99,235,.15);
  }
  .tv-person-select > button:disabled { opacity: .55; }

  /* ── Results count ── */
  .tv-results-info {
    font-size: 12px; color: var(--ink-3);
    margin-bottom: 8px;
    padding-left: 2px;
  }
  .tv-pagination {
    margin-top: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .tv-pagination-info {
    font-size: 12px;
    color: var(--ink-2);
    font-weight: 500;
  }

  /* ── Keyboard hint ── */
  .tv-kbd {
    display: inline-flex; align-items: center; justify-content: center;
    padding: 1px 5px; border-radius: 4px;
    background: #f1f5f9; border: 1px solid var(--border);
    font-size: 10px; color: var(--ink-3);
    font-family: monospace;
    line-height: 1.5;
  }

  /* ── Закреплённая задача ──
     Плавающая панель в духе macOS: матовое стекло с тонкой каймой, шапка-тулбар
     с кнопками-значками без рамок (как у панели помощника), внутри — списки iOS
     группами на сером полотне, действия — внизу. «Мои задачи», «Заметки»,
     «Оформление» и форма открываются внутри панели экранами со стрелкой «назад».
     В Chrome панель живёт в окне «поверх окон» (Document PiP) и занимает его целиком.

     Цвета в правилах — только переменные. Палитра (PIN_PALETTES) ставит их на
     корень панели, а генератор тёмного слоя сайта переписывает лишь правила с
     литералами: будь здесь хексы, светлая палитра на тёмном сайте получала бы
     тёмные кнопки поверх белой панели. Значения ниже — светлая палитра, на
     случай если своя не пришла. */
  .tv-pin-widget {
    --bg: #f5f5f7;
    --surface: #ffffff;
    --surface-2: #f8fafc;
    --border: #e2e8f0;
    --border-strong: #cbd5e1;
    --ink: #0f172a;
    --ink-2: #475569;
    --ink-3: #7b8597;
    --accent: #2563eb;
    --accent-fg: #ffffff;
    --select-bg: #eef4ff;
    --select-border: #bfd4fb;
    --select-ink: #1d4ed8;
    --fill: #eef1f5;
    --fill-strong: #e2e8f0;
    --amber: #d97706;
    --indigo: #4338ca;
    --emerald: #059669;
    --rose: #e11d48;
    --blue: #2563eb;
    --pin-panel: rgba(255, 255, 255, .94);
    --pin-bar: rgba(255, 255, 255, .8);
    --pin-line: rgba(15, 23, 42, .08);
    --pin-shadow: 0 18px 48px rgba(15, 23, 42, .16), 0 2px 8px rgba(15, 23, 42, .06);
    --pin-menu-shadow: 0 12px 32px rgba(15, 23, 42, .16), 0 2px 6px rgba(15, 23, 42, .08);
    --pin-danger: #e11d48;
    /* Текст-ссылки шапки («‹ Задача», «+ Новая») — темнее заливки главной кнопки:
       у «Мяты» и «Контраста» акцент на белом давал 3.4:1 для мелкого текста. */
    --pin-link: #2563eb;
    /* Бегунок сегментов: в «Тёмной» белый --surface сливался с дорожкой. */
    --pin-seg-thumb: #ffffff;
    /* Тона статусов, типов и срочности — те же, что у меток и кнопок раздела. */
    --tone-gray-bg: #eef1f5;    --tone-gray-ink: #475569;
    --tone-blue-bg: #dbeafe;    --tone-blue-ink: #1e40af;
    --tone-indigo-bg: #e0e7ff;  --tone-indigo-ink: #3730a3;
    --tone-violet-bg: #ede9fe;  --tone-violet-ink: #5b21b6;
    --tone-amber-bg: #fef3c7;   --tone-amber-ink: #92400e;
    --tone-emerald-bg: #d1fae5; --tone-emerald-ink: #065f46;
    --tone-rose-bg: #ffe4e6;    --tone-rose-ink: #9f1239;
    --tone-teal-bg: #ccfbf1;    --tone-teal-ink: #0f766e;
    --tone-fuchsia-bg: #fae8ff; --tone-fuchsia-ink: #86198f;
    --check-done: #10b981;
    /* Общие правила раздела (.tv-btn, .tv-input, заметки) берут скругления и
       тени отсюда: у панели нет предка .tv-root, и без этих переменных углы
       кнопок выходили прямыми. */
    --radius: 16px;
    --radius-sm: 12px;
    --radius-xs: 8px;
    --shadow-sm: 0 1px 2px rgba(15, 23, 42, .06), 0 1px 1px rgba(15, 23, 42, .04);
    --shadow-md: 0 4px 16px rgba(15, 23, 42, .08), 0 2px 6px rgba(15, 23, 42, .05);
    --shadow-lg: 0 20px 60px rgba(15, 23, 42, .14), 0 8px 24px rgba(15, 23, 42, .08);
    position: fixed;
    z-index: 130;
    width: min(380px, calc(100vw - 24px));
    max-height: calc(100vh - 24px);
    display: flex;
    flex-direction: column;
    background: var(--pin-panel);
    border: 1px solid var(--pin-line);
    border-radius: 18px;
    box-shadow: var(--pin-shadow);
    -webkit-backdrop-filter: saturate(180%) blur(24px);
    backdrop-filter: saturate(180%) blur(24px);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    font-size: 13px;
    line-height: 1.4;
    user-select: none;
    -webkit-font-smoothing: antialiased;
  }
  .tv-pin-widget *, .tv-pin-widget *::before, .tv-pin-widget *::after { box-sizing: border-box; }
  /* Окно «поверх окон»: рамку, тень и скругление даёт само окно. */
  .tv-pin-widget.is-detached {
    position: relative;
    inset: auto;
    width: 100%;
    height: 100vh;
    max-height: none;
    border: 0;
    border-radius: 0;
    box-shadow: none;
    background: var(--bg);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }
  .tv-pin-widget.is-dragging {
    box-shadow: var(--pin-shadow), 0 28px 72px rgba(15, 23, 42, .12);
  }
  /* Скругление держит внутренняя рамка: меню «Ещё» живёт рядом с ней и может
     выйти за край панели, а не обрезаться. */
  .tv-pin-frame {
    flex: 1 1 auto;
    min-height: 0;
    display: flex;
    flex-direction: column;
    border-radius: 17px;
    overflow: hidden;
  }
  .tv-pin-widget.is-detached .tv-pin-frame { border-radius: 0; }

  /* Шапка-тулбар. Тянется за всю шапку, как окно macOS, кроме кнопок. */
  .tv-pin-header {
    position: relative;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 6px;
    min-height: 44px;
    padding: 6px 8px 6px 10px;
    background: var(--pin-bar);
    border-bottom: 1px solid var(--pin-line);
    cursor: grab;
    touch-action: none;
  }
  .tv-pin-widget.is-dragging .tv-pin-header { cursor: grabbing; }
  .tv-pin-widget.is-detached .tv-pin-header { cursor: default; touch-action: auto; }
  .tv-pin-header.is-nav {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
    padding-left: 6px;
  }
  .tv-pin-grip {
    display: inline-flex;
    flex-shrink: 0;
    color: var(--ink-3);
    opacity: .75;
  }
  .tv-pin-heading {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
  .tv-pin-heading-title,
  .tv-pin-nav-title {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: -.01em;
    line-height: 1.25;
    color: var(--ink);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tv-pin-nav-center {
    display: flex;
    flex-direction: column;
    align-items: center;
    min-width: 0;
    max-width: 210px;
  }
  .tv-pin-nav-title { display: block; max-width: 100%; text-align: center; }
  .tv-pin-nav-sub {
    display: block;
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0;
    color: var(--ink-3);
    text-align: center;
  }
  .tv-pin-nav-side { display: flex; align-items: center; min-width: 0; }
  .tv-pin-nav-side.is-end { justify-content: flex-end; }
  .tv-pin-tools {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    flex-shrink: 0;
  }
  .tv-pin-tool {
    width: 28px;
    height: 28px;
    padding: 0;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: var(--ink-3);
    display: inline-grid;
    place-items: center;
    cursor: pointer;
    transition: background .15s ease, color .15s ease;
  }
  .tv-pin-tool:hover:not(:disabled),
  .tv-pin-tool.is-active {
    background: var(--fill);
    color: var(--ink-2);
  }
  .tv-pin-tool:disabled { opacity: .45; cursor: default; }
  /* «‹ Задача» и текстовые кнопки шапки — как в навигации iOS: акцентным цветом. */
  .tv-pin-back,
  .tv-pin-text-btn {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    min-width: 0;
    padding: 5px 8px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: var(--pin-link);
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
    cursor: pointer;
    transition: background .15s ease;
  }
  .tv-pin-back { padding-left: 2px; }
  .tv-pin-text-btn { gap: 4px; }
  .tv-pin-back:hover:not(:disabled),
  .tv-pin-text-btn:hover:not(:disabled) { background: var(--fill); }
  .tv-pin-text-btn:disabled { opacity: .45; cursor: default; }
  .tv-pin-tool:focus-visible,
  .tv-pin-back:focus-visible,
  .tv-pin-text-btn:focus-visible,
  .tv-pin-title-btn:focus-visible,
  .tv-pin-btn:focus-visible,
  .tv-pin-file:focus-visible,
  .tv-pin-list-row:focus-visible,
  .tv-pin-person:focus-visible,
  .tv-pin-segmented button:focus-visible,
  .tv-pin-look:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }

  /* Меню «Ещё» — как меню macOS: стекло, выбор строки акцентной заливкой. */
  .tv-pin-menu {
    position: absolute;
    top: 44px;
    right: 8px;
    z-index: 5;
    min-width: 224px;
    padding: 5px;
    background: var(--pin-panel);
    border: 1px solid var(--pin-line);
    border-radius: 12px;
    box-shadow: var(--pin-menu-shadow);
    -webkit-backdrop-filter: saturate(180%) blur(20px);
    backdrop-filter: saturate(180%) blur(20px);
    transform-origin: top right;
    animation: tvPinMenuIn .14s cubic-bezier(.22, .9, .3, 1);
  }
  /* Если окно «поверх окон» не дало себя увеличить — меню прокручивается, а не
     уходит за край окна. */
  .tv-pin-widget.is-detached .tv-pin-menu {
    max-height: calc(100vh - 52px);
    overflow-y: auto;
  }
  .tv-pin-menu.is-up {
    top: auto;
    bottom: calc(100% + 6px);
    transform-origin: bottom right;
  }
  @keyframes tvPinMenuIn {
    from { opacity: 0; transform: scale(.96); }
    to   { opacity: 1; transform: none; }
  }
  .tv-pin-menu-item {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 10px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--ink);
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  .tv-pin-menu-item svg { flex: none; color: var(--ink-3); }
  .tv-pin-menu-item:hover:not(:disabled),
  .tv-pin-menu-item:focus-visible {
    outline: none;
    background: var(--accent);
    color: var(--accent-fg);
  }
  .tv-pin-menu-item:hover:not(:disabled) svg,
  .tv-pin-menu-item:focus-visible svg { color: inherit; }
  .tv-pin-menu-item.is-danger,
  .tv-pin-menu-item.is-danger svg { color: var(--pin-danger); }
  .tv-pin-menu-item.is-danger:hover:not(:disabled),
  .tv-pin-menu-item.is-danger:focus-visible { background: var(--pin-danger); color: var(--accent-fg); }
  .tv-pin-menu-item.is-danger:hover:not(:disabled) svg,
  .tv-pin-menu-item.is-danger:focus-visible svg { color: inherit; }
  .tv-pin-menu-item:disabled { opacity: .45; cursor: default; }
  .tv-pin-menu-sep {
    height: 1px;
    margin: 4px 8px;
    background: var(--pin-line);
  }

  /* Содержимое — серое полотно, сведения — белыми группами, как в настройках iOS. */
  .tv-pin-scroll {
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 14px 12px 16px;
    background: var(--bg);
    scrollbar-width: thin;
  }
  /* У flex-потомка с overflow: hidden минимальная высота нулевая: при длинном
     содержимом группы «Описание» и «Исполнитель…» сжимались до полоски вместо
     прокрутки (замер на задаче с чек-листом и восемью файлами). */
  .tv-pin-scroll > * { flex-shrink: 0; }
  .tv-pin-scroll.is-compact { padding-bottom: 12px; }
  /* Заметки в окне «поверх окон» сами делят высоту окна: список сверху,
     редактор до низа. На странице — обычная прокрутка панели. */
  .tv-pin-widget.is-detached .tv-pin-scroll.is-notes { overflow: hidden; }
  .tv-pin-hero {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 0 4px;
  }
  .tv-pin-key {
    font-size: 11.5px;
    font-weight: 600;
    color: var(--ink-3);
    font-variant-numeric: tabular-nums;
    letter-spacing: .01em;
  }
  .tv-pin-title-btn {
    display: block;
    width: 100%;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--ink);
    text-align: left;
    cursor: pointer;
    transition: color .15s ease;
  }
  .tv-pin-title-btn:hover { color: var(--accent); }
  .tv-pin-title {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
    font-size: 16px;
    font-weight: 600;
    line-height: 1.3;
    letter-spacing: -.012em;
    word-break: break-word;
  }
  .tv-pin-widget:not(.is-expanded) .tv-pin-title { -webkit-line-clamp: 2; }
  .tv-pin-tags {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 5px;
  }
  .tv-pin-tag {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 99px;
    font-size: 11px;
    font-weight: 600;
    line-height: 1.3;
    white-space: nowrap;
    background: var(--tone-gray-bg);
    color: var(--tone-gray-ink);
  }
  .tv-pin-tag.is-blue    { background: var(--tone-blue-bg);    color: var(--tone-blue-ink); }
  .tv-pin-tag.is-indigo  { background: var(--tone-indigo-bg);  color: var(--tone-indigo-ink); }
  .tv-pin-tag.is-violet  { background: var(--tone-violet-bg);  color: var(--tone-violet-ink); }
  .tv-pin-tag.is-amber   { background: var(--tone-amber-bg);   color: var(--tone-amber-ink); }
  .tv-pin-tag.is-emerald { background: var(--tone-emerald-bg); color: var(--tone-emerald-ink); }
  .tv-pin-tag.is-rose    { background: var(--tone-rose-bg);    color: var(--tone-rose-ink); }
  .tv-pin-tag.is-teal    { background: var(--tone-teal-bg);    color: var(--tone-teal-ink); }
  .tv-pin-tag.is-fuchsia { background: var(--tone-fuchsia-bg); color: var(--tone-fuchsia-ink); }
  /* «Ждёт ответа» — та же метка, что в списке и на доске (точка и подпись, не
     капсула), только в тоне палитры: литералы раздела на «Тёмной» не читались бы. */
  .tv-pin-widget .tv-clar-waiting { color: var(--tone-violet-ink); }
  .tv-pin-widget .tv-clar-waiting::before { background: currentColor; }

  .tv-pin-section {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .tv-pin-section-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    padding: 0 6px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .04em;
    text-transform: uppercase;
    color: var(--ink-3);
  }
  .tv-pin-section-head small {
    font-size: 11px;
    font-weight: 500;
    color: var(--ink-2);
    letter-spacing: 0;
    text-transform: none;
    font-variant-numeric: tabular-nums;
  }
  .tv-pin-group {
    display: flex;
    flex-direction: column;
    background: var(--surface);
    border-radius: 12px;
    box-shadow: 0 0 0 1px var(--pin-line);
    overflow: hidden;
  }
  .tv-pin-description {
    margin: 0;
    padding: 10px 12px;
    max-height: 40vh;
    overflow-y: auto;
    font-size: 13px;
    line-height: 1.5;
    color: var(--ink-2);
    white-space: pre-wrap;
    word-break: break-word;
    user-select: text;
  }
  /* Строки списка: подпись слева, значение справа, разделитель — от текста. */
  .tv-pin-row,
  .tv-pin-check,
  .tv-pin-file,
  .tv-pin-list-row {
    position: relative;
  }
  .tv-pin-row + .tv-pin-row::before,
  .tv-pin-check + .tv-pin-check::before,
  .tv-pin-file + .tv-pin-file::before,
  .tv-pin-list-row + .tv-pin-list-row::before {
    content: '';
    position: absolute;
    top: 0;
    right: 0;
    left: 12px;
    height: 1px;
    background: var(--pin-line);
  }
  .tv-pin-check + .tv-pin-check::before { left: 42px; }
  .tv-pin-file + .tv-pin-file::before { left: 48px; }
  .tv-pin-list-row + .tv-pin-list-row::before { left: 30px; }
  .tv-pin-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 40px;
    padding: 9px 12px;
  }
  .tv-pin-row-label { flex-shrink: 0; color: var(--ink-2); }
  .tv-pin-row-value {
    min-width: 0;
    color: var(--ink);
    text-align: right;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tv-pin-row-value.is-overdue { color: var(--pin-danger); font-weight: 600; }
  .tv-pin-row.is-note { color: var(--ink-3); font-size: 12px; justify-content: flex-start; }

  /* Чек-лист — круглые отметки, как в «Напоминаниях». */
  .tv-pin-check {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 12px;
    color: var(--ink);
    line-height: 1.35;
    cursor: pointer;
  }
  .tv-pin-check:hover { background: var(--surface-2); }
  .tv-pin-check.is-disabled { cursor: default; }
  .tv-pin-check.is-disabled:hover { background: transparent; }
  .tv-pin-check-input {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: 0;
    opacity: 0;
    pointer-events: none;
  }
  .tv-pin-check-mark {
    flex: none;
    width: 20px;
    height: 20px;
    margin-top: -1px;
    border-radius: 50%;
    border: 1.5px solid var(--border-strong);
    display: grid;
    place-items: center;
    color: transparent;
    transition: background .15s ease, border-color .15s ease, color .15s ease;
  }
  .tv-pin-check.is-done .tv-pin-check-mark {
    background: var(--check-done);
    border-color: var(--check-done);
    color: var(--accent-fg);
  }
  .tv-pin-check.is-disabled .tv-pin-check-mark { opacity: .55; }
  .tv-pin-check-input:focus-visible + .tv-pin-check-mark {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--select-bg);
  }
  .tv-pin-check-title { flex: 1; min-width: 0; word-break: break-word; }
  .tv-pin-check.is-done .tv-pin-check-title { color: var(--ink-3); text-decoration: line-through; }

  /* Файлы — строками с цветной меткой типа, как список в окне просмотра. */
  .tv-pin-file {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px 8px 12px;
    border: 0;
    background: transparent;
    color: var(--ink);
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  .tv-pin-file:hover { background: var(--surface-2); }
  .tv-pin-file .tfv-badge {
    width: 26px;
    height: 26px;
    border-radius: 7px;
    font-size: 8px;
  }
  .tv-pin-file-name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tv-pin-chevron { flex: none; color: var(--ink-3); opacity: .8; }

  /* Нижняя панель действий. */
  .tv-pin-footer {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px 12px 12px;
    background: var(--pin-bar);
    border-top: 1px solid var(--pin-line);
  }
  .tv-pin-actions {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
    gap: 8px;
  }
  .tv-pin-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-height: 36px;
    padding: 8px 12px;
    border: 0;
    border-radius: 10px;
    background: var(--fill);
    color: var(--ink-2);
    font-size: 13px;
    font-weight: 600;
    line-height: 1.2;
    text-align: center;
    cursor: pointer;
    transition: box-shadow .15s ease, transform .1s ease, opacity .15s ease;
  }
  /* Наведение — лёгкое затемнение поверх своего тона (в тёмной палитре — высветление). */
  .tv-pin-btn:hover:not(:disabled) { box-shadow: inset 0 0 0 100px color-mix(in srgb, var(--ink) 6%, transparent); }
  .tv-pin-btn:active:not(:disabled) { transform: scale(.98); }
  .tv-pin-btn:disabled { opacity: .5; cursor: default; }
  .tv-pin-btn.is-primary { background: var(--accent); color: var(--accent-fg); }
  .tv-pin-btn.is-primary:hover:not(:disabled) { box-shadow: inset 0 0 0 100px color-mix(in srgb, var(--ink) 12%, transparent); }
  .tv-pin-btn.is-amber   { background: var(--tone-amber-bg);   color: var(--tone-amber-ink); }
  .tv-pin-btn.is-indigo  { background: var(--tone-indigo-bg);  color: var(--tone-indigo-ink); }
  .tv-pin-btn.is-emerald { background: var(--tone-emerald-bg); color: var(--tone-emerald-ink); }
  .tv-pin-btn.is-rose    { background: var(--tone-rose-bg);    color: var(--tone-rose-ink); }
  .tv-pin-hint {
    margin: 0;
    font-size: 12px;
    line-height: 1.4;
    color: var(--ink-3);
    text-align: center;
  }
  .tv-pin-error {
    margin: 0;
    font-size: 12px;
    line-height: 1.4;
    color: var(--pin-danger);
  }

  /* Отчёт при сдаче — внизу, на месте кнопок, как поле ответа в «Сообщениях». */
  .tv-pin-report { display: flex; flex-direction: column; gap: 8px; }
  .tv-pin-report-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .tv-pin-widget .tv-input,
  .tv-pin-widget .tv-textarea {
    background: var(--surface);
    border-color: var(--pin-line);
    border-radius: 10px;
    color: var(--ink);
    user-select: text;
  }
  .tv-pin-widget .tv-input:focus,
  .tv-pin-widget .tv-textarea:focus {
    background: var(--surface);
    border-color: var(--select-border);
    box-shadow: 0 0 0 3px var(--select-bg);
  }
  .tv-pin-widget .tv-input::placeholder,
  .tv-pin-widget .tv-textarea::placeholder { color: var(--ink-3); }
  .tv-pin-report .tv-textarea { min-height: 76px; }

  /* «Мои задачи»: сегменты, люди капсулами, задачи строками. */
  .tv-pin-segmented {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2px;
    padding: 2px;
    border-radius: 9px;
    background: var(--fill);
  }
  .tv-pin-segmented button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 5px 8px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--ink-2);
    font-size: 12.5px;
    font-weight: 500;
    cursor: pointer;
    transition: background .15s ease, color .15s ease, box-shadow .15s ease;
  }
  .tv-pin-segmented button:hover { color: var(--ink); }
  .tv-pin-segmented button.is-active {
    background: var(--pin-seg-thumb);
    color: var(--ink);
    box-shadow: 0 1px 3px rgba(15, 23, 42, .12), 0 0 0 .5px rgba(15, 23, 42, .04);
  }
  .tv-pin-count {
    font-size: 11px;
    font-weight: 500;
    color: var(--ink-3);
    font-variant-numeric: tabular-nums;
  }
  .tv-pin-people {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    margin: 0 -12px;
    padding: 1px 12px 3px;
    scrollbar-width: none;
  }
  .tv-pin-people::-webkit-scrollbar { display: none; }
  .tv-pin-person {
    flex: none;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 180px;
    padding: 3px 9px 3px 3px;
    border: 1px solid var(--pin-line);
    border-radius: 99px;
    background: var(--surface);
    color: var(--ink-2);
    font-size: 12.5px;
    font-weight: 500;
    cursor: pointer;
    transition: background .15s ease, color .15s ease, border-color .15s ease;
  }
  .tv-pin-person:hover { color: var(--ink); background: var(--surface-2); }
  .tv-pin-person.is-active {
    background: var(--select-bg);
    border-color: var(--select-border);
    color: var(--select-ink);
  }
  .tv-pin-person-avatar {
    flex: none;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    overflow: hidden;
    display: inline-grid;
    place-items: center;
    background: var(--fill-strong);
    color: var(--ink-2);
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
  }
  .tv-pin-person-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tv-pin-person-badge {
    flex: none;
    min-width: 17px;
    height: 17px;
    padding: 0 5px;
    border-radius: 99px;
    display: inline-grid;
    place-items: center;
    background: var(--fill-strong);
    color: var(--ink-2);
    font-size: 10.5px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .tv-pin-person-badge.is-alert { background: var(--tone-amber-bg); color: var(--tone-amber-ink); }
  .tv-pin-list-row {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 12px;
    border: 0;
    background: transparent;
    color: var(--ink);
    text-align: left;
    cursor: pointer;
  }
  .tv-pin-list-row:hover { background: var(--surface-2); }
  .tv-pin-dot {
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--ink-3);
  }
  .tv-pin-list-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .tv-pin-list-title {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
    font-size: 13px;
    font-weight: 500;
    line-height: 1.35;
    word-break: break-word;
  }
  .tv-pin-list-row.is-current .tv-pin-list-title { font-weight: 600; }
  .tv-pin-list-meta {
    font-size: 11.5px;
    color: var(--ink-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tv-pin-list-check { flex: none; color: var(--accent); }
  .tv-pin-empty {
    padding: 28px 16px;
    font-size: 12.5px;
    line-height: 1.45;
    color: var(--ink-3);
    text-align: center;
  }

  /* «Оформление» — плитки-образцы палитр с отметкой под ними, как выбор темы в iOS. */
  .tv-pin-appearance {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px 12px;
    padding: 2px 4px 0;
  }
  .tv-pin-look {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 0;
    border: 0;
    border-radius: 12px;
    background: transparent;
    color: var(--ink-2);
    cursor: pointer;
  }
  .tv-pin-look-frame {
    display: block;
    width: 100%;
    padding: 3px;
    border-radius: 14px;
    box-shadow: 0 0 0 1px transparent;
    transition: box-shadow .15s ease;
  }
  .tv-pin-look.is-active .tv-pin-look-frame { box-shadow: 0 0 0 2px var(--accent); }
  .tv-pin-look-preview {
    display: flex;
    flex-direction: column;
    aspect-ratio: 16 / 11;
    border-radius: 11px;
    overflow: hidden;
    background: var(--bg);
    box-shadow: 0 0 0 1px var(--pin-line), 0 1px 3px rgba(15, 23, 42, .08);
  }
  .tv-pin-look-bar {
    flex: 0 0 20%;
    background: var(--pin-bar);
    border-bottom: 1px solid var(--pin-line);
  }
  .tv-pin-look-body {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 6%;
    padding: 8% 9%;
  }
  .tv-pin-look-line {
    flex: 0 0 auto;
    height: 6px;
    width: 72%;
    border-radius: 3px;
    background: var(--ink);
    opacity: .82;
  }
  .tv-pin-look-line.is-short { width: 46%; background: var(--ink-3); opacity: .7; }
  .tv-pin-look-card {
    flex: 1;
    border-radius: 6px;
    background: var(--surface);
    box-shadow: 0 0 0 1px var(--pin-line);
  }
  .tv-pin-look-btn {
    flex: 0 0 auto;
    height: 9px;
    border-radius: 4px;
    background: var(--accent);
  }
  .tv-pin-look-name {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12.5px;
    font-weight: 500;
  }
  .tv-pin-look.is-active .tv-pin-look-name { color: var(--ink); font-weight: 600; }
  .tv-pin-look-radio {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    border: 1.5px solid var(--border-strong);
    display: inline-grid;
    place-items: center;
    color: transparent;
  }
  .tv-pin-look.is-active .tv-pin-look-radio {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--accent-fg);
  }

  /* Заметки внутри панели: рамку и подложку даёт панель, свои — не нужны. */
  /* Окно «поверх окон» уже 680px, и под него подходят телефонные правила раздела
     (отступ и предел высоты заметок) — здесь они перебиты: класс .is-compact
     в селекторе даёт правилам панели вес больше, чем у телефонных. */
  .tv-pin-widget .tv-notes-panel,
  .tv-pin-widget .tv-notes-panel.is-compact {
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    padding: 0;
  }
  .tv-pin-widget .tv-notes-panel.is-compact:not(.is-fullscreen) {
    max-height: none;
    overflow: visible;
  }
  .tv-pin-widget .tv-notes-panel.is-fullscreen { max-height: none; }
  /* Редактор — в свой рост, прокручивается весь столбец: сжатый до остатка окна
     редактор обрезал собственные поля и кнопку «Сохранить». */
  .tv-pin-widget .tv-notes-panel.is-fullscreen .tv-notes-editor {
    flex: none;
    min-height: auto;
  }
  .tv-pin-widget .tv-notes-editor-footer {
    flex-direction: row;
    align-items: center;
  }
  .tv-pin-widget .tv-notes-editor-actions { width: auto; }
  .tv-pin-widget .tv-notes-editor-actions .tv-btn { flex: none; }
  /* Общие кнопки и отметки раздела внутри панели (заметки, форма) — в тонах
     палитры: их светлые литералы на «Тёмной» горели бы пятнами, а синее
     наведение главной кнопки перекрашивало бы «Мяту» и «Контраст». */
  .tv-pin-widget .tv-btn-amber,
  .tv-pin-widget .tv-btn-amber:hover:not(:disabled)   { background: var(--tone-amber-bg);   color: var(--tone-amber-ink); }
  .tv-pin-widget .tv-btn-indigo,
  .tv-pin-widget .tv-btn-indigo:hover:not(:disabled)  { background: var(--tone-indigo-bg);  color: var(--tone-indigo-ink); }
  .tv-pin-widget .tv-btn-emerald,
  .tv-pin-widget .tv-btn-emerald:hover:not(:disabled) { background: var(--tone-emerald-bg); color: var(--tone-emerald-ink); }
  .tv-pin-widget .tv-btn-rose,
  .tv-pin-widget .tv-btn-rose:hover:not(:disabled)    { background: var(--tone-rose-bg);    color: var(--tone-rose-ink); }
  .tv-pin-widget .tv-btn-primary,
  .tv-pin-widget .tv-btn-primary:hover:not(:disabled) { background: var(--accent); border-color: var(--accent); color: var(--accent-fg); }
  .tv-pin-widget .tv-btn:hover:not(:disabled) { box-shadow: inset 0 0 0 100px color-mix(in srgb, var(--ink) 6%, transparent); }
  .tv-pin-widget .tv-note-due-pill { background: var(--tone-amber-bg); border-color: transparent; color: var(--tone-amber-ink); }
  .tv-pin-widget .tv-note-done-toggle { background: var(--tone-emerald-bg); border-color: transparent; color: var(--tone-emerald-ink); }
  .tv-pin-widget .tv-note-topic-btn.is-done { background: var(--tone-emerald-bg); border-color: transparent; }
  .tv-pin-widget .tv-note-topic-btn.is-done .tv-note-topic-title,
  .tv-pin-widget .tv-note-topic-btn.is-done .tv-note-topic-date { color: var(--tone-emerald-ink); }
  .tv-pin-widget .tv-notes-editor.is-done { background: var(--surface); }
  .tv-pin-widget .tv-notes-panel.is-compact:not(.is-fullscreen) .tv-notes-content {
    max-height: none;
    overflow: visible;
    padding-right: 0;
  }
  /* В окне «поверх окон» список и редактор делят всю высоту окна: общий
     предел .is-compact (до 100vh − 200px) оставлял под ними пустую полосу. */
  .tv-pin-widget .tv-notes-panel.is-fullscreen .tv-notes-content { max-height: none; }
  /* Узкое окно PiP попадает под телефонные правила раздела (≤680px) — там
     кнопки заметок растягивались на всю ширину. В панели — как на странице. */
  .tv-pin-widget .tv-notes-toolbar {
    flex-direction: row;
    align-items: center;
    justify-content: flex-end;
  }
  .tv-pin-widget .tv-notes-toolbar-actions { width: auto; }
  .tv-pin-widget .tv-notes-toolbar-actions .tv-btn { flex: none; }
  .tv-pin-widget .tv-note-column,
  .tv-pin-widget .tv-notes-editor {
    background: var(--surface);
    border-color: transparent;
    box-shadow: 0 0 0 1px var(--pin-line);
  }
  .tv-pin-widget .tv-note-topic-btn { border-color: var(--pin-line); }

  /* Быстрая форма: поля — белыми карточками на сером полотне. */
  .tv-pin-form {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .tv-pin-form .tv-form-field { margin: 0; }
  .tv-pin-form .tv-form-field > label {
    display: block;
    margin: 0 0 6px;
    padding: 0 4px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .04em;
    text-transform: uppercase;
    color: var(--ink-3);
  }
  .tv-pin-form .tv-textarea { min-height: 72px; }
  .tv-pin-form .tv-soft-block {
    background: var(--surface);
    border-color: transparent;
    border-radius: 12px;
    box-shadow: 0 0 0 1px var(--pin-line);
  }
  .tv-pin-switch-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-size: 13px;
    color: var(--ink);
    cursor: pointer;
  }
  .tv-pin-form .tv-composer-segmented { background: var(--fill); }
  .tv-pin-form .tv-composer-segmented button.is-active {
    background: var(--pin-seg-thumb);
    color: var(--ink);
    box-shadow: 0 1px 3px rgba(15, 23, 42, .12), 0 0 0 .5px rgba(15, 23, 42, .04);
  }
  .tv-pin-form .tv-person-select > button { background: var(--surface); border-color: var(--pin-line); border-radius: 10px; }
  .tv-pin-form .tv-file-dropzone { min-height: 72px; }
  .tv-pin-form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .tv-checklist-editor.is-compact {
    gap: 6px;
  }
  .tv-checklist-editor.is-compact .tv-checklist-editor-row {
    grid-template-columns: 32px minmax(0, 1fr) 34px;
    gap: 6px;
    padding: 6px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
  }
  .tv-checklist-editor.is-compact .tv-input {
    padding: 8px 9px;
    border-radius: 8px;
    background: var(--surface-2);
  }
  .tv-checklist-editor.is-compact .tv-checklist-required {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 34px;
    margin: 0;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-2);
    font-size: 0;
    line-height: 0;
  }
  .tv-checklist-editor.is-compact .tv-checklist-required input {
    width: 14px;
    height: 14px;
    margin: 0;
    flex: 0 0 auto;
  }
  .tv-checklist-editor.is-compact .tv-checklist-editor-remove {
    background: var(--surface-2);
  }
  .tv-checklist-editor.is-compact .tv-btn {
    align-self: stretch;
    justify-content: center;
    border-style: dashed;
    background: transparent;
  }
  .tv-file-dropzone-wrap {
    display: flex;
    flex-direction: column;
    gap: 7px;
  }
  .tv-file-dropzone {
    position: relative;
    display: grid;
    grid-template-columns: 34px minmax(0, 1fr) auto;
    align-items: center;
    gap: 9px;
    min-height: 64px;
    padding: 10px;
    border: 1.5px dashed var(--border-strong);
    border-radius: 10px;
    background: color-mix(in srgb, var(--surface-2) 78%, transparent);
    color: var(--ink-2);
    cursor: pointer;
    transition: border-color .15s ease, background .15s ease, color .15s ease, box-shadow .15s ease;
  }
  .tv-file-dropzone:hover,
  .tv-file-dropzone.is-dragging {
    border-color: var(--accent);
    background: var(--surface);
    color: var(--ink);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 11%, transparent);
  }
  .tv-file-dropzone.is-disabled {
    opacity: .58;
    cursor: not-allowed;
    box-shadow: none;
  }
  .tv-file-dropzone-input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
  }
  .tv-file-dropzone-icon {
    width: 34px;
    height: 34px;
    border-radius: 9px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--ink-2);
  }
  .tv-file-dropzone-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .tv-file-dropzone-copy strong {
    color: var(--ink);
    font-size: 12.5px;
    font-weight: 800;
  }
  .tv-file-dropzone-copy span {
    color: var(--ink-3);
    font-size: 11.5px;
  }
  .tv-file-dropzone-count {
    align-self: center;
    padding: 4px 8px;
    border-radius: 999px;
    background: #eef2ff;
    color: #3730a3;
    border: 1px solid #c7d2fe;
    font-size: 11px;
    font-weight: 800;
    white-space: nowrap;
  }
  .tv-selected-file-list {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .tv-selected-file-pill {
    display: grid;
    grid-template-columns: 18px minmax(0, 1fr) 26px;
    align-items: center;
    gap: 7px;
    padding: 6px 7px;
    border-radius: 9px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--ink-2);
  }
  .tv-selected-file-text {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .tv-selected-file-text strong {
    color: var(--ink);
    font-size: 12px;
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tv-selected-file-text span {
    color: var(--ink-3);
    font-size: 10.5px;
  }
  .tv-file-mark {
    color: var(--indigo);
    font-weight: 800;
  }
  .tv-file-remove-btn {
    width: 26px;
    height: 26px;
    border-radius: 7px;
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: var(--ink-3);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all .15s ease;
  }
  .tv-file-remove-btn:hover:not(:disabled) {
    color: var(--rose);
    background: #fff1f2;
    border-color: #fecdd3;
  }

  /* ═══ Новый вид раздела (16.11.2026): как приложения iOS — крупный заголовок,
     сегменты вместо вкладок, цифры виджетами, задачи сгруппированными списками,
     карточка задачи экраном из белых групп на сером полотне. Владелец выбрал
     этот вариант из двух («как iOS: заголовок и виджеты»).
     Только компьютер: у телефона свой слой (tasks-mobile.css) уже в этом стиле,
     и он опирается на разметку — поэтому разметка списков прежняя, а группы
     собираются соседством строк, как на телефоне. ═══ */

  /* ── Шапка: крупный заголовок, справа — круглые кнопки и «Новая задача» ── */
  body:not(.mobile-shell) .tv-root .tv-topbar {
    align-items: center;
    margin-bottom: 20px;
  }
  body:not(.mobile-shell) .tv-root .tv-topbar-title {
    font-size: 34px;
    font-weight: 700;
    letter-spacing: -0.026em;
    line-height: 1.05;
  }
  body:not(.mobile-shell) .tv-root .tv-topbar-lead { gap: 16px; }
  body:not(.mobile-shell) .tv-root .tv-topbar-actions { gap: 10px; }
  /* Проект и раздел доски — кнопки-меню iOS: серая заливка, без рамки. */
  body:not(.mobile-shell) .tv-root .tv-project-select > button,
  body:not(.mobile-shell) .tv-root .tv-section-select > button {
    border: 0;
    border-radius: 12px;
    background: var(--ios-fill);
    box-shadow: none;
    font-weight: 600;
    min-height: 38px;
  }
  body:not(.mobile-shell) .tv-root .tv-project-select > button:hover,
  body:not(.mobile-shell) .tv-root .tv-section-select > button:hover {
    background: var(--ios-fill-strong);
  }
  body:not(.mobile-shell) .tv-root .tv-topbar-lead > .tv-icon-btn {
    width: 38px; height: 38px; border-radius: 50%;
    border: 0; background: var(--ios-fill);
  }
  body:not(.mobile-shell) .tv-root .tv-topbar-lead > .tv-icon-btn:hover:not(:disabled) {
    background: var(--ios-fill-strong);
  }
  /* Круглые кнопки-значки шапки («Заметки», «Обновить») — как кнопки панели iOS. */
  body:not(.mobile-shell) .tv-root .tv-topbar-actions .tv-round-btn {
    display: inline-grid;
    place-items: center;
    width: 40px;
    height: 40px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: var(--surface);
    color: var(--ink-2);
    box-shadow: 0 0 0 1px rgba(15, 23, 42, .06), 0 1px 2px rgba(15, 23, 42, .06);
    cursor: pointer;
    transition: background .15s ease, color .15s ease, transform .1s ease;
  }
  body:not(.mobile-shell) .tv-root .tv-topbar-actions .tv-round-btn:hover:not(:disabled) {
    color: var(--ink);
    background: var(--surface-2);
  }
  body:not(.mobile-shell) .tv-root .tv-topbar-actions .tv-round-btn:active:not(:disabled) { transform: scale(.95); }
  body:not(.mobile-shell) .tv-root .tv-topbar-actions .tv-round-btn:disabled { opacity: .55; cursor: default; }
  body:not(.mobile-shell) .tv-root .tv-topbar-actions .tv-round-btn.is-active { color: #b45309; background: #fef3c7; }
  body:not(.mobile-shell) .tv-root .tv-topbar-actions .tv-round-btn.is-spinning svg { animation: tvSpin .8s linear infinite; }
  @keyframes tvSpin { to { transform: rotate(360deg); } }
  /* «Ждут вас» и «Новая задача» — капсулы. */
  body:not(.mobile-shell) .tv-root .tv-topbar-actions .tv-btn {
    min-height: 40px;
    border-radius: 999px;
    padding: 0 18px;
    font-size: 14px;
  }
  body:not(.mobile-shell) .tv-root .tv-topbar-actions .tv-btn-primary {
    box-shadow: 0 1px 2px rgba(37, 99, 235, .25), 0 4px 12px rgba(37, 99, 235, .18);
  }

  /* ── Сегменты «Обзор · Бэклог · Доска · Таймлайн» ── */
  body:not(.mobile-shell) .tv-root .tv-workspace-tabs {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    width: min(100%, 680px);
    gap: 0;
    padding: 2px;
    margin-bottom: 26px;
    border-radius: 11px;
    background: var(--ios-fill);
  }
  body:not(.mobile-shell) .tv-root .tv-workspace-tab {
    justify-content: center;
    min-height: 34px;
    padding: 0 10px;
    border-radius: 9px;
    font-size: 14px;
    font-weight: 600;
    color: var(--ink);
  }
  body:not(.mobile-shell) .tv-root .tv-workspace-tab { position: relative; }
  body:not(.mobile-shell) .tv-root .tv-workspace-tab:not(.is-active) { color: var(--ink-2); }
  /* Короткая черта между невыбранными сегментами, как в iOS. */
  body:not(.mobile-shell) .tv-root .tv-workspace-tab:not(.is-active) + .tv-workspace-tab:not(.is-active)::before {
    content: '';
    position: absolute;
    left: 0; top: 10px; bottom: 10px;
    width: 1px;
    background: var(--ios-sep);
  }
  body:not(.mobile-shell) .tv-root .tv-workspace-tab.is-active {
    background: var(--surface);
    box-shadow: 0 3px 8px rgba(0, 0, 0, .12), 0 3px 1px rgba(0, 0, 0, .04);
  }

  /* ── Цифры «Обзора» — одним рядом низких плиток (16.11 — «ниже ростом»):
     значок, подпись и число справа. Плитки белые, цвет — только у кружка со
     значком, как у смарт-списков «Напоминаний»: пять цветных градиентов подряд
     владелец назвал «слишком пёстро» (17.11). Тон кружка — переменной: литерал
     генератор тёмного слоя перекрасил бы с !important. ── */
  body:not(.mobile-shell) .tv-root .tv-stats-strip {
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 12px;
    margin-bottom: 34px;
  }
  body:not(.mobile-shell) .tv-root .tv-stat-card {
    --tile-tone: #64748b;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    grid-template-rows: auto;
    align-items: center;
    gap: 12px;
    min-height: 68px;
    padding: 12px 18px 12px 14px;
    border: 0;
    border-radius: 18px;
    color: var(--ink);
    background: var(--surface);
    box-shadow: 0 1px 2px rgba(15, 23, 42, .05), 0 0 0 1px rgba(15, 23, 42, .04);
  }
  /* Плитки не нажимаются — и не приподнимаются под мышью. */
  body:not(.mobile-shell) .tv-root .tv-stat-card:hover {
    transform: none;
    box-shadow: 0 1px 2px rgba(15, 23, 42, .05), 0 0 0 1px rgba(15, 23, 42, .04);
  }
  body:not(.mobile-shell) .tv-root .tv-stat-card.is-total { --tile-tone: #2563eb; }
  body:not(.mobile-shell) .tv-root .tv-stat-card.is-amber { --tile-tone: #f59e0b; }
  body:not(.mobile-shell) .tv-root .tv-stat-card.is-emerald { --tile-tone: #10b981; }
  body:not(.mobile-shell) .tv-root .tv-stat-card.is-returned { --tile-tone: #f43f5e; }
  body:not(.mobile-shell) .tv-root .tv-stat-card.is-backlog { --tile-tone: #8e8e93; }
  body:not(.mobile-shell) .tv-root .tv-stat-icon {
    grid-column: 1; grid-row: 1;
    display: inline-grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    color: var(--accent-fg);
    background: var(--tile-tone);
  }
  body:not(.mobile-shell) .tv-root .tv-stat-card .tv-stat-label {
    grid-column: 2; grid-row: 1;
    font-size: 14.5px;
    font-weight: 600;
    line-height: 1.25;
    letter-spacing: 0;
    text-transform: none;
    color: var(--ink-2);
  }
  body:not(.mobile-shell) .tv-root .tv-stat-card .tv-stat-value {
    grid-column: 3; grid-row: 1;
    justify-self: end;
    font-size: 28px;
    line-height: 1;
    color: var(--ink);
  }
  /* Окно уже 1200 px: пять плиток в ряд не вмещают значок, подпись и число в
     строку — «Выполнено» наезжало на цифру. Тогда значок и число сверху,
     подпись под ними; ряд остаётся одним и симметричным. */
  @media (max-width: 1199px) {
    body:not(.mobile-shell) .tv-root .tv-stat-card {
      grid-template-columns: auto minmax(0, 1fr);
      grid-template-rows: auto auto;
      gap: 8px 10px;
      padding: 12px 14px;
    }
    body:not(.mobile-shell) .tv-root .tv-stat-card .tv-stat-icon { width: 28px; height: 28px; }
    body:not(.mobile-shell) .tv-root .tv-stat-card .tv-stat-value {
      grid-column: 2; grid-row: 1;
      font-size: 24px;
    }
    body:not(.mobile-shell) .tv-root .tv-stat-card .tv-stat-label {
      grid-column: 1 / -1; grid-row: 2;
      font-size: 13.5px;
    }
  }

  /* ── Заголовки «Мои задачи», «Все задачи» — крупные, как в списках iOS ── */
  body:not(.mobile-shell) .tv-root .tv-section { margin-bottom: 38px; }
  body:not(.mobile-shell) .tv-root .tv-section-header { margin-bottom: 12px; }
  body:not(.mobile-shell) .tv-root .tv-section-title {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.015em;
    text-transform: none;
    color: var(--ink);
  }

  /* «Принятые / Исходящие» — малые сегменты. */
  body:not(.mobile-shell) .tv-root .tv-my-tabs {
    display: inline-grid;
    grid-template-columns: 1fr 1fr;
    min-width: 340px;
    padding: 2px;
    margin-bottom: 14px;
    border: 0;
    border-radius: 9px;
    background: var(--ios-fill);
    transform: none;
  }
  body:not(.mobile-shell) .tv-root .tv-tab-btn {
    justify-content: center;
    min-height: 30px;
    border-radius: 7px;
    font-size: 13px;
    font-weight: 600;
  }
  body:not(.mobile-shell) .tv-root .tv-tab-btn.is-active {
    background: var(--surface);
    box-shadow: 0 3px 8px rgba(0, 0, 0, .12), 0 3px 1px rgba(0, 0, 0, .04);
  }

  /* ── Люди «Моих задач» — лентой аватаров, как закреплённые чаты в «Сообщениях»
     (16.11, выбор владельца). Лента переносится на следующую строку, а не
     прокручивается вбок: у руководителя людей много, и искать человека
     прокруткой хуже, чем глазами. ── */
  body:not(.mobile-shell) .tv-root .tv-people {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 2px;
    margin: 0 -10px;
  }
  body:not(.mobile-shell) .tv-root .tv-people-item {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    width: 100px;
    padding: 10px 6px;
    border: 0;
    border-radius: 16px;
    background: transparent;
    color: var(--ink);
    cursor: pointer;
    transition: background .15s ease;
  }
  body:not(.mobile-shell) .tv-root .tv-people-item:hover { background: var(--ios-fill); }
  body:not(.mobile-shell) .tv-root .tv-people-item:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, .35);
  }
  body:not(.mobile-shell) .tv-root .tv-people-avatar { position: relative; display: block; }
  body:not(.mobile-shell) .tv-root .tv-people-avatar .tv-avatar-md {
    width: 60px;
    height: 60px;
    font-size: 18px;
    color: var(--ink-2);
    background: var(--surface);
    box-shadow: 0 0 0 1px rgba(15, 23, 42, .06), 0 1px 3px rgba(15, 23, 42, .08);
  }
  /* Число на аватаре — как значок непрочитанного в iOS, с кольцом цвета фона. */
  body:not(.mobile-shell) .tv-root .tv-people-badge {
    --people-badge: #64748b;
    position: absolute;
    top: -3px;
    right: -6px;
    display: inline-grid;
    place-items: center;
    min-width: 22px;
    height: 22px;
    padding: 0 6px;
    border-radius: 11px;
    background: var(--people-badge);
    color: var(--accent-fg);
    font-size: 12px;
    font-weight: 700;
    line-height: 1;
    font-variant-numeric: tabular-nums;
    box-shadow: 0 0 0 2px var(--bg);
  }
  body:not(.mobile-shell) .tv-root .tv-people-badge.is-alert { --people-badge: #e11d48; }
  body:not(.mobile-shell) .tv-root .tv-people-name {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
    max-width: 100%;
    font-size: 12.5px;
    font-weight: 500;
    line-height: 1.25;
    text-align: center;
    overflow-wrap: anywhere;
    color: var(--ink);
  }
  /* Подсказка: кто это и сколько задач в каком состоянии. Появляется с задержкой,
     чтобы не мелькать, пока мышь идёт через ленту. */
  body:not(.mobile-shell) .tv-root .tv-people-tip {
    --tip-x: -50%;
    position: absolute;
    top: calc(100% + 2px);
    left: 50%;
    z-index: 30;
    display: grid;
    gap: 4px;
    padding: 10px 12px;
    border-radius: 12px;
    background: var(--surface);
    box-shadow: 0 10px 28px rgba(15, 23, 42, .16), 0 0 0 1px rgba(15, 23, 42, .06);
    text-align: left;
    white-space: nowrap;
    opacity: 0;
    visibility: hidden;
    pointer-events: none;
    transform: translate(var(--tip-x), -4px);
    transition: opacity .15s ease, transform .15s ease, visibility 0s linear .15s;
  }
  body:not(.mobile-shell) .tv-root .tv-people-item[data-tip="start"] .tv-people-tip { --tip-x: 0%; left: 0; }
  body:not(.mobile-shell) .tv-root .tv-people-item[data-tip="end"] .tv-people-tip { --tip-x: 0%; left: auto; right: 0; }
  body:not(.mobile-shell) .tv-root .tv-people-item:hover .tv-people-tip,
  body:not(.mobile-shell) .tv-root .tv-people-item:focus-visible .tv-people-tip {
    opacity: 1;
    visibility: visible;
    transform: translate(var(--tip-x), 0);
    transition: opacity .15s ease .3s, transform .15s ease .3s, visibility 0s linear .3s;
  }
  body:not(.mobile-shell) .tv-root .tv-people-tip-name {
    margin-bottom: 2px;
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink);
  }
  body:not(.mobile-shell) .tv-root .tv-people-tip-row {
    display: flex;
    justify-content: space-between;
    gap: 18px;
    font-size: 12.5px;
    color: var(--ink-2);
  }
  body:not(.mobile-shell) .tv-root .tv-people-tip-row b {
    font-weight: 700;
    color: var(--ink);
    font-variant-numeric: tabular-nums;
  }

  /* ── Поиск и фильтры ── */
  body:not(.mobile-shell) .tv-root .tv-search-input {
    min-height: 40px;
    border: 0;
    border-radius: 12px;
    background: var(--ios-fill);
    font-size: 14px;
  }
  body:not(.mobile-shell) .tv-root .tv-search-input:focus {
    background: var(--surface);
    box-shadow: 0 0 0 3px rgba(37, 99, 235, .18), 0 0 0 1px #93c5fd;
  }

  /* ── Фильтры «Все задачи» — три кнопки-меню вместо 11 капсул (16.11) ── */
  body:not(.mobile-shell) .tv-root .tv-filter-menus {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  body:not(.mobile-shell) .tv-root .tv-filter-menu {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 40px;
    padding: 0 12px 0 16px;
    border: 0;
    border-radius: 12px;
    background: var(--ios-fill);
    color: var(--ink);
    font-size: 14px;
    font-weight: 600;
    white-space: nowrap;
    cursor: pointer;
    transition: background .15s ease, box-shadow .15s ease;
  }
  body:not(.mobile-shell) .tv-root .tv-filter-menu svg { color: var(--ink-3); }
  body:not(.mobile-shell) .tv-root .tv-filter-menu:hover,
  body:not(.mobile-shell) .tv-root .tv-filter-menu[aria-expanded="true"] { background: var(--ios-fill-strong); }
  /* Выбранный фильтр — мягким голубым, как выбор везде на сайте. */
  body:not(.mobile-shell) .tv-root .tv-filter-menu.is-set,
  body:not(.mobile-shell) .tv-root .tv-filter-menu.is-set:hover,
  body:not(.mobile-shell) .tv-root .tv-filter-menu.is-set[aria-expanded="true"] {
    background: var(--select-bg);
    color: var(--select-ink);
    box-shadow: inset 0 0 0 1px var(--select-border);
  }
  body:not(.mobile-shell) .tv-root .tv-filter-menu.is-set svg { color: var(--select-ink); }
  body:not(.mobile-shell) .tv-root .tv-filter-menu:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, .35);
  }
  body:not(.mobile-shell) .tv-root .tv-filter-reset {
    min-height: 40px;
    padding: 0 10px;
    border: 0;
    border-radius: 10px;
    background: none;
    color: var(--accent);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
  }
  body:not(.mobile-shell) .tv-root .tv-filter-reset:hover { background: var(--ios-fill); }

  /* ── Задачи — карточками в сетке, как «Заметки» в режиме галереи (вид
     «Галерея», 16.11); дата — заголовком над своей сеткой. Разметка прежняя и
     плоская — разделители дат и строки вперемешку: на неё опирается телефон. ── */
  body:not(.mobile-shell) .tv-root .tv-task-list {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 12px;
  }
  body:not(.mobile-shell) .tv-root .tv-task-date-separator {
    grid-column: 1 / -1;
    margin: 22px 0 0 16px;
    font-size: 12.5px;
    letter-spacing: .03em;
  }
  body:not(.mobile-shell) .tv-root .tv-task-list > .tv-task-date-separator:first-child { margin-top: 2px; }
  body:not(.mobile-shell) .tv-root .tv-task-date-separator::after { content: none; }
  /* Карточка: сверху кружок и статус словом (булавка справа), посередине тема и
     люди, внизу дата и метки «Проблема»/«Критичная». Всё сверху одной строкой
     переносилось у карточек с метками, и ряд карточек съезжал. */
  body:not(.mobile-shell) .tv-root .tv-task-row:not(.is-compact) {
    display: grid;
    grid-template-columns: 22px minmax(0, 1fr) auto auto;
    grid-template-rows: auto 1fr auto;
    grid-template-areas:
      "check state state pin"
      "main main main main"
      "date date tag prio";
    align-items: center;
    gap: 12px 10px;
    min-height: 168px;
    padding: 14px 16px;
    border: 0;
    border-radius: 16px;
    background: var(--surface);
    transform: none;
    box-shadow: 0 1px 2px rgba(15, 23, 42, .05), 0 0 0 1px rgba(15, 23, 42, .04);
  }
  body:not(.mobile-shell) .tv-root .tv-task-row:not(.is-compact):hover {
    background: #fafafc;
    transform: none;
    box-shadow: 0 8px 20px rgba(15, 23, 42, .09), 0 0 0 1px rgba(15, 23, 42, .05);
  }
  /* Значки и дата лежат в одной обёртке с булавкой — она раскрыта, и каждый
     встаёт в свою область карточки. */
  body:not(.mobile-shell) .tv-root .tv-task-row-meta { display: contents; }
  body:not(.mobile-shell) .tv-root .tv-task-row-check { grid-area: check; }
  body:not(.mobile-shell) .tv-root .tv-task-row-state { grid-area: state; justify-self: start; }
  body:not(.mobile-shell) .tv-root .tv-row-pin-btn { grid-area: pin; justify-self: end; }
  body:not(.mobile-shell) .tv-root .tv-task-row-meta > .tv-badge { grid-area: prio; justify-self: end; }
  body:not(.mobile-shell) .tv-root .tv-task-row-meta > .tv-badge:has(+ .tv-badge) { grid-area: tag; }
  /* Шеврон «перейти» карточке не нужен: она нажимается целиком. */
  body:not(.mobile-shell) .tv-root .tv-task-row-meta > svg:last-child { display: none; }
  /* Дата переносится, а не наезжает на метки: «Предложение» и «Критичная» вместе
     в самой узкой карточке оставляют ей меньше её ширины. */
  body:not(.mobile-shell) .tv-root .tv-task-row-date {
    grid-area: date;
    justify-self: start;
    min-width: 0;
    font-size: 12.5px;
    white-space: normal;
    text-align: left;
    color: var(--ink-3);
  }
  body:not(.mobile-shell) .tv-root .tv-task-row-main { grid-area: main; align-self: start; gap: 8px; }
  body:not(.mobile-shell) .tv-root .tv-task-row-subject {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    white-space: normal;
    font-size: 16px;
    font-weight: 600;
    line-height: 1.3;
    letter-spacing: -0.005em;
  }
  /* Подзаголовок переносится второй строкой, а не режет людей и сроки: на окне
     ~900 px имена сжимались в ноль, а «Ждёт ответа» уходил за край. */
  body:not(.mobile-shell) .tv-root .tv-task-row-flow {
    font-size: 12.5px;
    gap: 2px 6px;
    flex-wrap: wrap;
    white-space: normal;
  }
  body:not(.mobile-shell) .tv-root .tv-task-row-flow .tv-deadline-chip,
  body:not(.mobile-shell) .tv-root .tv-task-row-flow .tv-clar-waiting { flex-shrink: 0; }
  body:not(.mobile-shell) .tv-root .tv-task-row-flow .tv-task-row-assignee-chip { min-width: 48px; }
  body:not(.mobile-shell) .tv-root .tv-task-row-flow .tv-task-row-assignee-chip { font-size: 12.5px; color: var(--ink-3); max-width: 220px; }
  body:not(.mobile-shell) .tv-root .tv-task-row-flow .tv-deadline-chip,
  body:not(.mobile-shell) .tv-root .tv-task-row-flow .tv-clar-waiting { font-size: 12px; }
  body:not(.mobile-shell) .tv-root .tv-task-row-flow .tv-deadline-chip::before { content: '·'; margin-right: 6px; color: var(--ink-3); font-weight: 400; }
  body:not(.mobile-shell) .tv-root .tv-task-row-flow .tv-task-row-flow-name { max-width: 220px; min-width: 48px; }

  /* Кружок статуса слева — как отметка в «Напоминаниях»: контур в цвет статуса,
     у выполненных и принятых — заливка с галочкой. */
  .tv-task-row-check {
    flex: none;
    display: inline-grid;
    place-items: center;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    border: 2px solid var(--check-indigo);
    color: var(--accent-fg);
  }
  /* Цвета колец — переменными: это акценты статусов, в тёмной теме они те же. */
  .tv-task-row-check.is-amber { border-color: var(--check-amber); }
  .tv-task-row-check.is-amber::after {
    content: '';
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--check-amber);
  }
  .tv-task-row-check.is-rose { border-color: var(--check-rose); }
  .tv-task-row-check.is-violet { border-color: var(--check-violet); background: var(--check-violet); }
  .tv-task-row-check.is-emerald { border-color: var(--check-emerald); background: var(--check-emerald); }
  /* Статус справа — словом в его цвете, без капсулы: кружок слева уже цветной. */
  .tv-task-row-state {
    display: inline-flex;
    align-items: center;
    font-size: 13px;
    font-weight: 600;
    white-space: nowrap;
    color: var(--ink-2);
  }
  /* Цвета статуса словом — переменными: светлый индиго генератор тёмной темы не
     перекрашивал, и «Выставлен» (самый частый статус) на графите давал 2,5:1. */
  .tv-task-row-state.is-indigo { color: var(--state-indigo); }
  .tv-task-row-state.is-amber { color: var(--state-amber); }
  .tv-task-row-state.is-violet { color: var(--state-violet); }
  .tv-task-row-state.is-emerald { color: var(--state-emerald); }
  .tv-task-row-state.is-rose { color: var(--state-rose); }
  body:not(.mobile-shell) .tv-root .tv-row-pin-btn { opacity: 0; transition: opacity .15s ease; }
  body:not(.mobile-shell) .tv-root .tv-task-row:hover .tv-row-pin-btn,
  body:not(.mobile-shell) .tv-root .tv-row-pin-btn.is-pinned,
  body:not(.mobile-shell) .tv-root .tv-row-pin-btn:focus-visible { opacity: 1; }

  /* Пустой список и постраничка. */
  body:not(.mobile-shell) .tv-root .tv-empty {
    border: 0;
    border-radius: 14px;
    background: var(--surface);
    box-shadow: 0 0 0 1px rgba(15, 23, 42, .04);
  }
  body:not(.mobile-shell) .tv-root .tv-pagination .tv-btn { border-radius: 999px; }
  body:not(.mobile-shell) .tv-root .tv-results-info { margin: 0 0 6px 16px; }

  /* ── Доска, бэклог, таймлайн: списки панели — кнопки-меню iOS, как проект в шапке ── */
  body:not(.mobile-shell) .tv-root .tb-toolbar .tb-scope-select:not(.inline-flex) > button,
  body:not(.mobile-shell) .tv-root .tb-toolbar .tb-sort-select > button,
  body:not(.mobile-shell) .tv-root .tb-toolbar .tb-chunk-select > button {
    border: 0;
    border-radius: 12px;
    background: var(--ios-fill);
    box-shadow: none;
    font-weight: 600;
    min-height: 38px;
  }
  body:not(.mobile-shell) .tv-root .tb-toolbar .tb-scope-select:not(.inline-flex) > button:hover,
  body:not(.mobile-shell) .tv-root .tb-toolbar .tb-sort-select > button:hover,
  body:not(.mobile-shell) .tv-root .tb-toolbar .tb-chunk-select > button:hover {
    background: var(--ios-fill-strong);
  }
  body:not(.mobile-shell) .tv-root .tb-toolbar { margin-bottom: 6px; }

  /* ── Карточка задачи — экран iOS: серое полотно, сведения и блоки белыми группами ── */
  body:not(.mobile-shell) .tv-drawer { width: min(600px, 100vw); background: var(--bg); }
  body:not(.mobile-shell) .tv-drawer-header {
    padding: 22px 22px 18px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }
  body:not(.mobile-shell) .tv-drawer-title { font-size: 22px; font-weight: 700; letter-spacing: -0.015em; line-height: 1.25; margin-bottom: 10px; }
  body:not(.mobile-shell) .tv-drawer-header-actions .tv-icon-btn,
  body:not(.mobile-shell) .tv-drawer-header-actions .tv-close-btn {
    width: 34px; height: 34px; border-radius: 50%;
    border: 0; background: var(--ios-fill);
  }
  body:not(.mobile-shell) .tv-drawer-header-actions .tv-icon-btn:hover:not(:disabled),
  body:not(.mobile-shell) .tv-drawer-header-actions .tv-close-btn:hover { background: var(--ios-fill-strong); }
  body:not(.mobile-shell) .tv-drawer-header-actions .tv-icon-btn-danger { background: #fff1f2; }
  body:not(.mobile-shell) .tv-drawer-header-actions .tv-icon-btn.is-pinned { background: #eef2ff; }
  body:not(.mobile-shell) .tv-drawer-body {
    gap: 14px;
    padding: 18px 18px 22px;
    background: var(--bg);
  }
  body:not(.mobile-shell) .tv-drawer-body > hr.tv-divider { display: none; }
  /* Каждый блок карточки — белая группа со скруглением. */
  body:not(.mobile-shell) .tv-drawer-body > div,
  body:not(.mobile-shell) .tv-drawer-body > section {
    flex-shrink: 0;
    padding: 14px 16px;
    border-radius: 14px;
    background: var(--surface);
    box-shadow: 0 0 0 1px rgba(15, 23, 42, .04);
  }
  body:not(.mobile-shell) .tv-drawer-body > .tv-soft-block { border: 0; }
  /* Участники — строками с лицами. */
  /* Участники — в две колонки: столбиком они вместе со строками сведений
     уводили «Историю» под нижнюю панель кнопок. */
  body:not(.mobile-shell) .tv-drawer-body > .tv-participants {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0;
    padding: 4px 0;
  }
  body:not(.mobile-shell) .tv-drawer-body > .tv-participants .tv-participant {
    position: relative;
    flex: none;
    gap: 12px;
    min-width: 0;
    padding: 8px 16px;
  }
  body:not(.mobile-shell) .tv-drawer-body .tv-participant-avatar { width: 36px; height: 36px; font-size: 12.5px; }
  body:not(.mobile-shell) .tv-drawer-body .tv-participant-info { flex-direction: column-reverse; }
  body:not(.mobile-shell) .tv-drawer-body .tv-participant-role {
    font-size: 12px; font-weight: 500; letter-spacing: 0; text-transform: none; color: var(--ink-3);
  }
  body:not(.mobile-shell) .tv-drawer-body .tv-participant-name { font-size: 15px; font-weight: 500; }
  /* Сведения — строки «подпись — значение», как в «Настройках». */
  body:not(.mobile-shell) .tv-drawer-body > .tv-info-grid {
    display: flex;
    flex-direction: column;
    gap: 0;
    padding: 2px 0;
  }
  body:not(.mobile-shell) .tv-drawer-body > .tv-info-grid .tv-info-item {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    min-height: 40px;
    padding: 6px 16px;
  }
  body:not(.mobile-shell) .tv-drawer-body > .tv-info-grid .tv-info-item + .tv-info-item::before {
    content: '';
    position: absolute;
    top: 0; right: 0; left: 16px;
    height: 1px;
    background: var(--border);
  }
  body:not(.mobile-shell) .tv-drawer-body > .tv-info-grid .tv-info-item > label {
    margin: 0;
    font-size: 14px;
    font-weight: 400;
    letter-spacing: 0;
    text-transform: none;
    color: var(--ink-2);
  }
  body:not(.mobile-shell) .tv-drawer-body > .tv-info-grid .tv-info-item > span {
    font-size: 14px;
    text-align: right;
  }
  body:not(.mobile-shell) .tv-drawer-body .tv-block-label {
    font-size: 12px;
    letter-spacing: .04em;
    margin-bottom: 10px;
  }
  body:not(.mobile-shell) .tv-drawer-body .tv-description { font-size: 14.5px; line-height: 1.6; color: var(--ink); }
  body:not(.mobile-shell) .tv-drawer-footer {
    padding: 14px 18px;
    background: var(--surface);
  }
  body:not(.mobile-shell) .tv-drawer-footer .tv-btn { min-height: 40px; border-radius: 12px; padding: 0 18px; font-size: 14px; }

  @media (max-width: 680px) {
    .tv-root { padding: 16px 12px; }
    .tv-stats-strip { grid-template-columns: repeat(2, 1fr); }
    .tv-drawer { width: 100vw; }
    .tv-info-grid { grid-template-columns: 1fr; }
    .tv-task-row-meta { gap: 6px; padding-left: 6px; }
    .tv-task-row-assignee-chip, .tv-task-row-flow, .tv-task-row-date { display: none; }
    .tv-task-row-meta .tv-badge:nth-of-type(3), .tv-deadline-chip { display: none; }
    .tv-pagination { flex-wrap: wrap; justify-content: center; }
    .tv-person-row { flex-wrap: wrap; }
    .tv-person-stats { white-space: normal; gap: 8px; }
    .tv-participants { flex-direction: column; gap: 10px; }
    /* В колонке flex-basis 180px становился ВЫСОТОЙ участника: между
       исполнителем и постановщиком зияло по 180 px пустоты. */
    .tv-participant { flex: none; }
    .tv-pin-widget {
      width: min(380px, calc(100vw - 16px));
    }
    .tv-checklist-editor:not(.is-compact) .tv-checklist-editor-row,
    .tv-pin-form-grid {
      grid-template-columns: minmax(0, 1fr);
    }
    .tv-checklist-editor:not(.is-compact) .tv-checklist-editor-row {
      grid-template-columns: minmax(0, 1fr) 34px;
    }
    .tv-checklist-editor:not(.is-compact) .tv-checklist-required {
      grid-column: 1 / -1;
    }
    .tv-notes-panel,
    .tv-notes-panel.is-compact {
      padding: 10px;
    }
    .tv-notes-content,
    .tv-notes-board,
    .tv-note-controls-grid {
      grid-template-columns: 1fr;
    }
    .tv-notes-toolbar,
    .tv-notes-editor-footer {
      align-items: flex-start;
      flex-direction: column;
    }
    .tv-notes-toolbar-actions,
    .tv-notes-editor-actions {
      width: 100%;
    }
    .tv-notes-toolbar-actions .tv-btn,
    .tv-notes-editor-actions .tv-btn {
      justify-content: center;
      flex: 1;
    }
  }
  @media (max-width: 400px) {
    .tv-stats-strip { grid-template-columns: 1fr 1fr; gap: 8px; }
    .tv-stat-value { font-size: 22px; }
    .tv-topbar-title { font-size: 18px; }
    .tv-topbar-lead { width: 100%; }
    .tv-project-select, .tv-section-select { flex: 1; width: auto; min-width: 0; }
  }
`;
document.head.appendChild(styleTag);

const TASK_VIEW_QUERY_PARAM = 'view';
const TASK_ID_QUERY_PARAM = 'task_id';
// Ключ задачи проекта в адресе (?task=HR-125, задача #401): по ссылке сразу видно,
// чья это задача. У задач без проекта — прежний ?task_id=N.
const TASK_KEY_QUERY_PARAM = 'task';

/* В адрес — ключ задачи проекта или её номер; второй параметр снимаем, чтобы в
   ссылке не жили два разных указателя на задачу. */
const setTaskInUrl = (url, task) => {
  const key = taskKeyOf(task);
  const taskId = Number(task?.id || 0);
  url.searchParams.set(TASK_VIEW_QUERY_PARAM, 'tasks');
  if (key) {
    url.searchParams.set(TASK_KEY_QUERY_PARAM, key);
    url.searchParams.delete(TASK_ID_QUERY_PARAM);
  } else {
    url.searchParams.set(TASK_ID_QUERY_PARAM, String(taskId));
    url.searchParams.delete(TASK_KEY_QUERY_PARAM);
  }
};

const buildTaskDeepLink = (task) => {
  if (typeof window === 'undefined') return '';
  const normalizedTaskId = Number(task?.id || 0);
  if (!Number.isInteger(normalizedTaskId) || normalizedTaskId <= 0) return '';
  try {
    const url = new URL(window.location.href);
    /* Ссылку копируют и отправляют людям: метки перезагрузки (?v=, ?auth_reload=)
       в ней не нужны — см. utils/urlHygiene.js. */
    stripTechnicalQueryParams(url);
    setTaskInUrl(url, task);
    return url.toString();
  } catch (error) {
    return '';
  }
};

const syncTaskDeepLink = (task) => {
  if (typeof window === 'undefined') return;
  try {
    const url = new URL(window.location.href);
    stripTechnicalQueryParams(url);
    if (task?.id) {
      setTaskInUrl(url, task);
    } else {
      url.searchParams.delete(TASK_ID_QUERY_PARAM);
      url.searchParams.delete(TASK_KEY_QUERY_PARAM);
    }
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  } catch (error) {
    // Ignore URL-sync failures in constrained browser contexts.
  }
};

/* ─── Constants ─── */
/* «Продакшн» (задача #354) — четвёртый тип. В «Типе» основной формы он есть
   всегда, а исполнители у него — только маркетинг (TaskComposerForm). В быстрой
   форме закреплённой задачи полей брифа нет, поэтому там BASIC_TAG_OPTIONS. */
const TAG_OPTIONS = [
  { value: 'task',       label: 'Задача' },
  { value: 'problem',    label: 'Проблема' },
  { value: 'suggestion', label: 'Предложение' },
  { value: PRODUCTION_TAG, label: 'Продакшн' },
];
const BASIC_TAG_OPTIONS = TAG_OPTIONS.filter((option) => option.value !== PRODUCTION_TAG);

const TAG_META = {
  task:       { label: 'Задача',      badge: 'tv-badge-blue' },
  problem:    { label: 'Проблема',    badge: 'tv-badge-rose' },
  suggestion: { label: 'Предложение', badge: 'tv-badge-teal' },
  [PRODUCTION_TAG]: { label: 'Продакшн', badge: 'tv-badge-fuchsia' },
};

const PRIORITY_OPTIONS = [
  { value: 'normal',   label: 'Обычная' },
  { value: 'urgent',   label: 'Срочная' },
  { value: 'critical', label: 'Критичная' },
];

const PRIORITY_META = {
  normal:   { label: 'Обычная',   badge: 'tv-badge-gray',   chipCls: 'is-active' },
  urgent:   { label: 'Срочная',   badge: 'tv-badge-amber',  chipCls: 'is-active-amber' },
  critical: { label: 'Критичная', badge: 'tv-badge-rose',   chipCls: 'is-active-rose' },
};

const RECURRENCE_OPTIONS = [
  { value: 'daily',   label: 'Ежедневно' },
  { value: 'weekly',  label: 'Еженедельно' },
  { value: 'monthly', label: 'Ежемесячно' },
];

/* Палитры закреплённой задачи («Оформление» в её меню). Каждая задаёт ВСЕ цвета
   панели: в окне «поверх окон» тёмного слоя сайта нет, и недостающая переменная
   осталась бы светлой — у прежней «Тёмной» так светлыми были кнопки и метки, а
   у «Светлой» главная кнопка была чёрной. Тона меток светлые палитры берут из
   CSS панели (как у меток раздела), тёмная — свои, системные цвета iOS. */
const PIN_PALETTES = [
  {
    id: 'paper',
    label: 'Светлая',
    vars: {
      '--bg': '#f5f5f7',
      '--surface': '#ffffff',
      '--surface-2': '#f8fafc',
      '--border': '#e2e8f0',
      '--border-strong': '#cbd5e1',
      '--ink': '#0f172a',
      '--ink-2': '#475569',
      '--ink-3': '#7b8597',
      '--accent': '#2563eb',
      '--accent-fg': '#ffffff',
      '--select-bg': '#eef4ff',
      '--select-border': '#bfd4fb',
      '--select-ink': '#1d4ed8',
      '--fill': '#eef1f5',
      '--fill-strong': '#e2e8f0',
      '--pin-panel': 'rgba(255, 255, 255, 0.94)',
      '--pin-bar': 'rgba(255, 255, 255, 0.8)',
      '--pin-line': 'rgba(15, 23, 42, 0.08)',
      '--pin-link': '#2563eb',
      '--pin-seg-thumb': '#ffffff',
    }
  },
  {
    id: 'midnight',
    label: 'Тёмная',
    vars: {
      '--bg': '#1c1c1e',
      '--surface': '#2c2c2e',
      '--surface-2': '#333336',
      '--border': '#3a3a3c',
      '--border-strong': '#545458',
      '--ink': '#f5f5f7',
      '--ink-2': '#c7c7cc',
      '--ink-3': '#98989f',
      '--accent': '#0a84ff',
      '--accent-fg': '#ffffff',
      '--select-bg': 'rgba(10, 132, 255, 0.2)',
      '--select-border': 'rgba(10, 132, 255, 0.5)',
      '--select-ink': '#64b5ff',
      '--fill': 'rgba(118, 118, 128, 0.24)',
      '--fill-strong': 'rgba(118, 118, 128, 0.36)',
      '--amber': '#ffb340',
      '--indigo': '#a5b4fc',
      '--emerald': '#30d158',
      '--rose': '#ff6482',
      '--blue': '#64d2ff',
      '--pin-panel': 'rgba(30, 30, 32, 0.94)',
      '--pin-bar': 'rgba(44, 44, 46, 0.82)',
      '--pin-line': 'rgba(255, 255, 255, 0.1)',
      '--pin-link': '#409cff',
      '--pin-seg-thumb': '#636366',
      '--pin-shadow': '0 18px 48px rgba(0, 0, 0, 0.5), 0 2px 8px rgba(0, 0, 0, 0.3)',
      '--pin-menu-shadow': '0 12px 32px rgba(0, 0, 0, 0.5), 0 2px 6px rgba(0, 0, 0, 0.3)',
      '--pin-danger': '#ff453a',
      '--check-done': '#30d158',
      '--tone-gray-bg': 'rgba(118, 118, 128, 0.24)',
      '--tone-gray-ink': '#c7c7cc',
      '--tone-blue-bg': 'rgba(10, 132, 255, 0.22)',
      '--tone-blue-ink': '#8ec5ff',
      '--tone-indigo-bg': 'rgba(94, 92, 230, 0.3)',
      '--tone-indigo-ink': '#c0bdff',
      '--tone-violet-bg': 'rgba(191, 90, 242, 0.24)',
      '--tone-violet-ink': '#dcb4fb',
      '--tone-amber-bg': 'rgba(255, 159, 10, 0.2)',
      '--tone-amber-ink': '#ffcc70',
      '--tone-emerald-bg': 'rgba(48, 209, 88, 0.18)',
      '--tone-emerald-ink': '#86e3a0',
      '--tone-rose-bg': 'rgba(255, 55, 95, 0.2)',
      '--tone-rose-ink': '#ff9fb2',
      '--tone-teal-bg': 'rgba(64, 200, 224, 0.18)',
      '--tone-teal-ink': '#8ee3f0',
      '--tone-fuchsia-bg': 'rgba(218, 143, 255, 0.2)',
      '--tone-fuchsia-ink': '#efb8ff',
      '--shadow-sm': '0 1px 2px rgba(0, 0, 0, 0.4)',
      '--shadow-md': '0 4px 16px rgba(0, 0, 0, 0.45)',
      '--shadow-lg': '0 20px 60px rgba(0, 0, 0, 0.6)',
    }
  },
  {
    id: 'mint',
    label: 'Мята',
    vars: {
      '--bg': '#eef8f3',
      '--surface': '#ffffff',
      '--surface-2': '#f4fbf7',
      '--border': '#cdeadb',
      '--border-strong': '#9fd6bb',
      '--ink': '#0b3b2a',
      '--ink-2': '#2f6651',
      '--ink-3': '#5d8a78',
      '--accent': '#0f9d6c',
      '--accent-fg': '#ffffff',
      '--select-bg': '#e2f6ec',
      '--select-border': '#a5e0c4',
      '--select-ink': '#0b7a53',
      '--fill': '#e3f2ea',
      '--fill-strong': '#d1e9dd',
      '--pin-panel': 'rgba(255, 255, 255, 0.94)',
      '--pin-bar': 'rgba(250, 255, 252, 0.82)',
      '--pin-line': 'rgba(11, 59, 42, 0.1)',
      '--pin-link': '#0b7a53',
      '--pin-seg-thumb': '#ffffff',
    }
  },
  {
    id: 'focus',
    label: 'Контраст',
    vars: {
      '--bg': '#fff6ec',
      '--surface': '#fffdf9',
      '--surface-2': '#fff3e6',
      '--border': '#f8dcc0',
      '--border-strong': '#f2b98a',
      '--ink': '#3b1405',
      '--ink-2': '#8a3412',
      '--ink-3': '#a86a45',
      '--accent': '#ea580c',
      '--accent-fg': '#ffffff',
      '--select-bg': '#ffedd5',
      '--select-border': '#fdba74',
      '--select-ink': '#c2410c',
      '--fill': '#fdecd9',
      '--fill-strong': '#fbdcbc',
      '--pin-panel': 'rgba(255, 253, 249, 0.94)',
      '--pin-bar': 'rgba(255, 250, 244, 0.84)',
      '--pin-line': 'rgba(67, 20, 7, 0.1)',
      '--pin-link': '#c2410c',
      '--pin-seg-thumb': '#fffdf9',
    }
  },
];

/* Явный выбор оформления. Ключ новый: прежняя панель писала в
   'otp:pinned-task-palette' активную палитру при каждом показе — то есть
   «Светлую» всем подряд, и такой записи нельзя верить как выбору человека.
   Из старого ключа переносим только не-«Светлую»: её выбирали сами. */
const PIN_PALETTE_STORAGE_KEY = 'otp:pinned-task-palette:v2';
const PIN_PALETTE_LEGACY_KEY = 'otp:pinned-task-palette';

const readPinPaletteChoice = () => {
  try {
    const storage = window.localStorage;
    const saved = storage.getItem(PIN_PALETTE_STORAGE_KEY);
    if (PIN_PALETTES.some((palette) => palette.id === saved)) return saved;
    const legacy = storage.getItem(PIN_PALETTE_LEGACY_KEY);
    if (legacy === null) return '';
    storage.removeItem(PIN_PALETTE_LEGACY_KEY);
    if (legacy !== 'paper' && PIN_PALETTES.some((palette) => palette.id === legacy)) {
      storage.setItem(PIN_PALETTE_STORAGE_KEY, legacy);
      return legacy;
    }
  } catch (error) {
    // Хранилище недоступно — оформление как у сайта.
  }
  return '';
};

/* Тон капсулы и кнопки панели — из той же метки раздела: tv-badge-indigo →
   is-indigo, tv-btn-amber → is-amber. Отдельная таблица разошлась бы с метками. */
const PIN_TONES = new Set(['gray', 'blue', 'indigo', 'violet', 'amber', 'emerald', 'rose', 'teal', 'fuchsia', 'primary']);
const pinToneOf = (cls) => {
  const tone = String(cls || '').replace(/^tv-(badge|btn)-/, '');
  return PIN_TONES.has(tone) ? tone : '';
};

/* Капсула человека в «Моих задачах»: «Фамилия И.» — одной фамилии мало для
   однофамильцев, полное ФИО не влезает. Полное — в подсказке и заголовке группы. */
const pinPersonShortName = (name) => {
  const words = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '—';
  return words.length > 1 ? `${words[0]} ${words[1].charAt(0)}.` : words[0];
};

const STATUS_META = {
  assigned:    { label: 'Выставлен',  badge: 'tv-badge-indigo',  dot: '#a5b4fc', chipCls: 'is-active-indigo' },
  in_progress: { label: 'В работе',   badge: 'tv-badge-amber',   dot: '#fcd34d', chipCls: 'is-active-amber' },
  completed:   { label: 'Выполнен',   badge: 'tv-badge-violet',  dot: '#c4b5fd', chipCls: 'is-active-violet' },
  accepted:    { label: 'Принят',     badge: 'tv-badge-emerald', dot: '#6ee7b7', chipCls: 'is-active-emerald' },
  returned:    { label: 'Возвращён',  badge: 'tv-badge-rose',    dot: '#fda4af', chipCls: 'is-active-rose' },
};

const DONE_STATUSES       = new Set(['completed', 'accepted']);
const ACTIVE_STATUSES     = new Set(['in_progress', 'returned']);
const NOT_ACCEPTED_STATUSES = new Set(['assigned']);

const HISTORY_LABELS = {
  assigned:    'Выставлен',
  in_progress: 'Принят в работу',
  completed:   'Выполнен',
  accepted:    'Принят',
  returned:    'Возвращён на доработку',
  reopened:    'Возобновлён',
};

/* Подпись рядом с именем в списке исполнителей. Фолбэк печатает СЫРОЕ значение
   из базы, поэтому карту дополняем вместе с каждой ролью, попадающей в
   получатели: без строк бэк-офиса в выпадашке стояло бы «Иванова (hr_manager)». */
// Наблюдателей у задачи не больше двадцати — тот же потолок, что в task_projects.py.
const TASK_MAX_WATCHERS = 20;

const ROLE_LABELS = {
  super_admin: 'Супер-админ',
  admin: 'Админ',
  sv: 'СВ',
  trainer: 'Тренер',
  hr_manager: 'HR',
  accounting_manager: 'Бухгалтерия',
  marketing_manager: 'Маркетинг',
};
/* Рядовой IT в получателях — «оператор» или «стажёр» (своей должности у отдела
   нет), и подпись по роли читалась бы как линия. Его подписываем отделом, как
   бэк-офис: «Иванов (IT)». Отдел приходит в списке получателей
   (department_code); у исполнителя, взятого из самой задачи, его может не быть —
   тогда «Сотрудник», но не сырое «operator». */
const RECIPIENT_DEPARTMENT_LABELS = {
  it: 'IT',
  // Рядовой отдела разработки с «Задачами» поимённо (TASK_EMPLOYEE_USER_GRANTS).
  development: 'Разработка',
  // Старые учётки бэк-офиса на 'operator' (заведены до своих должностей) —
  // тем же словом, что их коллеги по должности в ROLE_LABELS.
  hr: 'HR',
  accounting: 'Бухгалтерия',
  marketing: 'Маркетинг',
};
const recipientRoleLabel = (person) => {
  const role = String(person?.role || '').trim().toLowerCase();
  if (role === 'operator' || role === 'trainee') {
    const departmentCode = String(person?.department_code || '').trim().toLowerCase();
    return RECIPIENT_DEPARTMENT_LABELS[departmentCode] || 'Сотрудник';
  }
  return ROLE_LABELS[role] || role;
};
const TASKS_PAGE_SIZE = 20;

/** «3ч», «1д 2ч», «90», «2h30m» → минуты. Пусто/мусор → 0. */
const parseSpentInput = (raw) => {
  const text = String(raw ?? '').trim().toLowerCase().replace(',', '.');
  if (!text) return 0;
  if (/^\d+(\.\d+)?$/.test(text)) return Math.round(Number(text));
  const units = { d: 1440, д: 1440, h: 60, ч: 60, m: 1, м: 1 };
  let total = 0;
  let matched = false;
  for (const match of text.matchAll(/(\d+(?:\.\d+)?)\s*([dдhчmм])/g)) {
    total += Number(match[1]) * units[match[2]];
    matched = true;
  }
  return matched ? Math.round(total) : 0;
};

const formatSpentMinutes = (minutes) => {
  const total = Math.max(0, Math.round(Number(minutes) || 0));
  if (!total) return '';
  if (total < 60) return `${total} мин`;
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (hours < 24) return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `${days} д ${restHours} ч` : `${days} д`;
};

const REPORT_KIND_LABEL = { progress: 'Промежуточный', completion: 'Итоговый' };

/* Выбранный отдел переживает перезагрузку: это рабочий контекст человека, а не
   разовый фильтр. Ключ привязан к пользователю — на общем компьютере чужой
   выбор не должен подменять свой. */
const readStoredValue = (key) => {
  try { return window.localStorage.getItem(key) || ''; }
  catch (error) { return ''; }
};
const writeStoredValue = (key, value) => {
  try { window.localStorage.setItem(key, String(value || '')); }
  catch (error) { /* private mode */ }
};

const WORKSPACE_TAB_STORAGE_KEY = 'otp.tasks.workspaceTab';

const WORKSPACE_TABS = [
  { id: 'overview', label: 'Обзор' },
  { id: 'backlog',  label: 'Бэклог' },
  { id: 'board',    label: 'Доска' },
  { id: 'timeline', label: 'Таймлайн' },
];

/**
 * Итог принимает поручитель (или постановщик, если поручителя нет) — исполнитель
 * свою работу себе не принимает. Исключение — своя инициатива: принимать больше некому,
 * иначе задача навсегда осталась бы на проверке. Те же правила проверяет API.
 */
export const canReviewTask = (task, currentUserId, currentUserRole) => {
  // Задача коллеги-СВ (#298), участника проекта или наблюдателя (#401) — только на просмотр.
  if (task?.viewer_is_observer) return false;
  const creatorId = Number(task?.creator?.id || 0);
  const requesterId = Number(task?.requested_by?.id || 0);
  const authorityId = requesterId || creatorId;
  // Свою работу не принимает НИ ОДИН из исполнителей, а не только первый.
  const isAssignee = isTaskAssignee(task, currentUserId);

  if (authorityId && authorityId === currentUserId) return true;
  if (isAssignee) return false;
  return isAdminLikeRole(currentUserRole) || isSupervisorRole(currentUserRole) || creatorId === currentUserId;
};

const buildTaskActionButtons = (task, currentUserId, currentUserRole) => {
  // Задача коллеги-СВ (#298) или задача проекта, которую человек только видит (#401):
  // смотреть можно, действовать — нет.
  if (task?.viewer_is_observer) return [];
  const creatorId  = Number(task?.creator?.id  || 0);
  // Принять в работу и сдать может любой исполнитель — они равноправны.
  const isAssignee = isTaskAssignee(task, currentUserId);
  const isCreator  = creatorId === currentUserId;
  const isSuperAdmin = normalizeRole(currentUserRole) === 'super_admin';
  const isAdmin    = isAdminLikeRole(normalizeRole(currentUserRole));
  const canReview  = canReviewTask(task, currentUserId, currentUserRole);
  const s = task?.status;
  const btns = [];
  /* Вернуть в бэклог можно только не начатую задачу — те же права, что и у
     переноса карточки мышью на доске (постановщик или админ). */
  if ((isAdmin || isCreator) && s === 'assigned' && !task?.is_backlog)
    btns.push({ action: 'to_backlog', label: 'В бэклог', cls: 'tv-btn-ghost' });
  if (isAssignee && (s === 'assigned' || s === 'returned'))
    btns.push({ action: 'in_progress', label: 'Принять в работу', cls: 'tv-btn-amber' });
  if (isAssignee && (s === 'in_progress' || s === 'returned'))
    btns.push({ action: 'completed', label: 'Отметить выполненной', cls: 'tv-btn-indigo' });
  if (canReview && s === 'completed') {
    btns.push({ action: 'accepted', label: 'Принять', cls: 'tv-btn-emerald' });
    btns.push({ action: 'returned', label: 'Вернуть', cls: 'tv-btn-rose' });
  }
  if (canReview && s === 'accepted')
    btns.push({ action: 'reopened', label: 'Возобновить', cls: 'tv-btn-ghost' });
  if (isCreator)
    btns.push({ action: 'edit', label: 'Редактировать', cls: 'tv-btn-ghost' });
  if (isSuperAdmin)
    btns.push({ action: 'delete', label: 'Удалить', cls: 'tv-btn-rose' });
  return btns;
};

const fmt = (v) => {
  if (!v) return '—';
  const d = new Date(v);
  return isNaN(d) ? String(v) : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
};

const fmtShortDateTime = (v) => {
  if (!v) return '';
  const d = new Date(v);
  return isNaN(d) ? String(v) : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

const isTaskOverdue = (task) => {
  if (!task?.due_at || DONE_STATUSES.has(task?.status)) return false;
  const due = new Date(task.due_at).getTime();
  return Number.isFinite(due) && due < Date.now();
};

const taskDeadlineLabel = (task) => {
  if (!task?.due_at) return '';
  return `${isTaskOverdue(task) ? 'Просрочено' : 'Дедлайн'}: ${fmtShortDateTime(task.due_at)}`;
};

const createEmptyChecklistDraftItem = () => ({ title: '', is_required: true });

const normalizeChecklistItems = (items) => {
  const source = Array.isArray(items) ? items : String(items || '').split('\n');
  const seen = new Set();
  return source
    .map((item) => {
      const title = String((typeof item === 'string' ? item : item?.title) || '').trim();
      if (!title) return null;
      const key = title.toLocaleLowerCase('ru-RU');
      if (seen.has(key)) return null;
      seen.add(key);
      /* id пункта, пришедшего с сервера: по нему правка узнаёт пункт и не
         снимает его отметку, даже если пункт переименовали (HR-2, 24.11.2026). */
      const id = typeof item === 'object' ? Number(item?.id || 0) : 0;
      return {
        ...(id > 0 ? { id } : {}),
        title,
        is_required: typeof item === 'object' ? item?.is_required !== false : true,
      };
    })
    .filter(Boolean);
};

/* Чек-лист — как его видит правка: какие пункты, в каком порядке, обязательны ли. */
const checklistKey = (items) => JSON.stringify(
  normalizeChecklistItems(items).map((item) => [item.id || 0, item.title, item.is_required]),
);

const checklistToFormItems = (items) => {
  const normalized = normalizeChecklistItems(items);
  return normalized.length ? normalized : [createEmptyChecklistDraftItem()];
};

const updateChecklistDraftItem = (items, index, patch) => {
  const list = Array.isArray(items) && items.length ? items : [createEmptyChecklistDraftItem()];
  return list.map((item, itemIndex) => (
    itemIndex === index ? { ...item, ...patch } : item
  ));
};

const appendChecklistDraftItem = (items) => [
  ...(Array.isArray(items) ? items : []),
  createEmptyChecklistDraftItem(),
];

const removeChecklistDraftItem = (items, index) => {
  const next = (Array.isArray(items) ? items : []).filter((_, itemIndex) => itemIndex !== index);
  return next.length ? next : [createEmptyChecklistDraftItem()];
};

const checklistProgress = (items) => {
  const list = Array.isArray(items) ? items : [];
  const done = list.filter((item) => item?.is_done).length;
  return { done, total: list.length };
};

const LOCAL_NOTES_EVENT = 'otp:task-notes-changed';
const localNotesKey = (userId) => `otp:task-notes:${Number(userId || 0)}`;
/* ── Напоминания в Telegram перед дедлайном ──
   Предел жёсткий: максимум за сутки, дальше напоминание бесполезно. */
const REMINDER_MAX_MINUTES = 1440;
const REMINDER_OPTIONS = [
  { value: 0,    label: 'Без напоминания' },
  { value: 30,   label: 'За 30 минут' },
  { value: 60,   label: 'За час' },
  { value: 180,  label: 'За 3 часа' },
  { value: 360,  label: 'За 6 часов' },
  { value: 1440, label: 'За день' },
];
/* Те же варианты для списка сайта (CustomSelect сверяет значения строками). В форме
   задачи подпись полная — поле там одно, без отдельной метки «Напомнить». */
const REMINDER_SELECT_OPTIONS = REMINDER_OPTIONS.map((option) => ({ value: String(option.value), label: option.label }));
const COMPOSER_REMINDER_OPTIONS = REMINDER_OPTIONS.map((option) => ({
  value: String(option.value),
  label: option.value ? `Напомнить в Telegram: ${option.label.toLowerCase()}` : 'Без напоминания',
}));
const REMINDER_DEFAULT_MINUTES = 1440;

const normalizeReminderMinutes = (value) => {
  const minutes = Number(value);
  if (!Number.isFinite(minutes) || minutes <= 0) return null;
  return Math.min(REMINDER_MAX_MINUTES, Math.round(minutes));
};

const reminderLabel = (minutes) => {
  const normalized = normalizeReminderMinutes(minutes);
  if (!normalized) return '';
  return REMINDER_OPTIONS.find((option) => option.value === normalized)?.label
    || `За ${formatSpentMinutes(normalized)}`;
};

const EMPTY_TASK_NOTE = {
  id: '',
  title: '',
  body: '',
  priority: 'normal',
  due_at: null,
  is_task: false,
  is_done: false,
  saved_at: null,
  created_at: null,
  completed_at: null,
  reminder_minutes_before: null,
  is_local: false,
};

const createDraftNoteId = () => (
  `draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
);

const isPersistedNoteId = (noteId) => /^\d+$/.test(String(noteId || '').trim());

const normalizeTaskNotePriority = (value) => {
  const priority = String(value || 'normal').trim().toLowerCase();
  return PRIORITY_META[priority] ? priority : 'normal';
};

const normalizeTaskNote = (value) => {
  const source = value && typeof value === 'object' ? value : {};
  const id = String(source.id || createDraftNoteId());
  const isTask = Boolean(source.is_task);
  return {
    ...EMPTY_TASK_NOTE,
    id,
    title: String(source.title || '').slice(0, 160),
    body: String(source.body || source.text || ''),
    priority: normalizeTaskNotePriority(source.priority),
    due_at: source.due_at || source.deadline || null,
    is_task: isTask,
    is_done: isTask && Boolean(source.is_done),
    saved_at: source.saved_at || source.updated_at || null,
    created_at: source.created_at || source.saved_at || source.updated_at || new Date().toISOString(),
    completed_at: source.completed_at || null,
    // Напоминание имеет смысл только при дедлайне.
    reminder_minutes_before: (source.due_at || source.deadline)
      ? normalizeReminderMinutes(source.reminder_minutes_before)
      : null,
    is_local: Boolean(source.is_local || !isPersistedNoteId(id)),
  };
};

const createEmptyTaskNote = () => ({
  ...EMPTY_TASK_NOTE,
  id: createDraftNoteId(),
  title: 'Новая заметка',
  created_at: new Date().toISOString(),
  is_local: true,
});

const compareNotesByUpdatedDesc = (a, b) => (
  (new Date(b?.saved_at || b?.updated_at || b?.created_at || 0).getTime() || 0) -
  (new Date(a?.saved_at || a?.updated_at || a?.created_at || 0).getTime() || 0)
);

const NOTE_PRIORITY_MINUTES = { normal: 0, urgent: 12 * 60, critical: 24 * 60 };

const noteRelevanceScore = (note) => {
  if (!note?.due_at) return Number.POSITIVE_INFINITY;
  const dueMs = new Date(note.due_at).getTime();
  if (!Number.isFinite(dueMs)) return Number.POSITIVE_INFINITY;
  const minutesUntilDue = (dueMs - Date.now()) / 60000;
  const boundedDeadline = Math.max(-7 * 24 * 60, minutesUntilDue);
  const priorityBoost = NOTE_PRIORITY_MINUTES[normalizeTaskNotePriority(note.priority)] || 0;
  const donePenalty = note.is_done ? 365 * 24 * 60 : 0;
  return boundedDeadline - priorityBoost + donePenalty;
};

const compareNotesByRelevance = (a, b) => {
  const byScore = noteRelevanceScore(a) - noteRelevanceScore(b);
  if (byScore !== 0) return byScore;
  return compareNotesByUpdatedDesc(a, b);
};

const normalizeTaskNotesList = (value) => {
  const raw = Array.isArray(value) ? value : (Array.isArray(value?.notes) ? value.notes : []);
  const seen = new Set();
  return raw
    .map(normalizeTaskNote)
    .filter((note) => {
      if (!note.id || seen.has(note.id)) return false;
      seen.add(note.id);
      return true;
    })
    .sort(compareNotesByUpdatedDesc);
};

const readLocalNotes = (userId) => {
  if (typeof window === 'undefined' || !userId) return [];
  try {
    const raw = window.localStorage.getItem(localNotesKey(userId));
    if (!raw) return [];
    return normalizeTaskNotesList(JSON.parse(raw));
  } catch (error) {
    return [];
  }
};

const writeLocalNotes = (userId, notes) => {
  if (typeof window === 'undefined' || !userId) return;
  const normalized = normalizeTaskNotesList(notes);
  try {
    window.localStorage.setItem(localNotesKey(userId), JSON.stringify({ version: 1, notes: normalized }));
    window.dispatchEvent(new CustomEvent(LOCAL_NOTES_EVENT, {
      detail: { userId: Number(userId || 0), notes: normalized },
    }));
  } catch (error) {
    // Local notes are best-effort browser state.
  }
};

const toDatetimeLocalValue = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  const pad = (part) => String(part).padStart(2, '0');
  return [
    date.getFullYear(),
    '-',
    pad(date.getMonth() + 1),
    '-',
    pad(date.getDate()),
    'T',
    pad(date.getHours()),
    ':',
    pad(date.getMinutes()),
  ].join('');
};

const noteDeadlineLabel = (note) => {
  if (!note?.due_at) return '';
  const dueMs = new Date(note.due_at).getTime();
  if (!Number.isFinite(dueMs)) return fmtShortDateTime(note.due_at);
  const diffMinutes = Math.round((dueMs - Date.now()) / 60000);
  const absoluteLabel = fmtShortDateTime(note.due_at);
  if (diffMinutes < 0) return `Просрочено: ${absoluteLabel}`;
  if (diffMinutes < 60) return `Осталось ${Math.max(1, diffMinutes)} мин`;
  if (diffMinutes < 24 * 60) return `Сегодня: ${absoluteLabel}`;
  if (diffMinutes < 48 * 60) return `Завтра: ${absoluteLabel}`;
  return `Дедлайн: ${absoluteLabel}`;
};

const buildNotePayload = (note) => {
  const normalized = normalizeTaskNote(note);
  const isTask = Boolean(normalized.is_task);
  return {
    title: String(normalized.title || '').trim() || 'Без темы',
    body: normalized.body || '',
    priority: normalizeTaskNotePriority(normalized.priority),
    due_at: normalized.due_at || null,
    is_task: isTask,
    is_done: isTask && Boolean(normalized.is_done),
    reminder_minutes_before: normalized.due_at ? normalized.reminder_minutes_before : null,
  };
};

const EMPTY_TASK_FORM = {
  subject: '',
  description: '',
  tag: 'task',
  priority: 'normal',
  assigneeIds: [],
  /* «Все» (владелец 24.11.2026): состав держит сама форма — все люди выбранной
     доски (её состав), кроме постановщика (себя — «Себе»). На сервер уходит тем
     же списком assignee_ids; false — поштучно. */
  assignAll: false,
  /* Дедлайн — днём и временем, строкой поля «ГГГГ-ММ-ДДTЧЧ:ММ» по часам Алматы
     (так же их отдаёт и принимает сервер); '' — без срока. initial* — с чем
     задача пришла в правку: срок и напоминание уходят, только если их меняли. */
  dueAt: '',
  initialDueAt: '',
  initialReminderMinutes: '',
  /* У повторяющейся задачи (шаблона регламента) — начало ближайшего повтора:
     её «Дедлайн» в форме — срок этого повтора, а на сервер уходит смещение от
     его начала (deadline_duration_minutes). '' — задача не повторяется. */
  repeatFrom: '',
  /* Повтор регламента (итерация): id шаблона, от которого задача родилась.
     Своего расписания у неё нет — «Повтор» в форме не показываем. */
  regulationParentId: '',
  // С каким повтором задача пришла в правку ('' — новая задача, шлём всегда).
  initialRecurrence: '',
  isRegulation: false,
  recurrenceType: '',
  recurrenceInterval: '1',
  checklistItems: [],
  // С каким чек-листом задача пришла в правку ('' — новая задача, шлём всегда).
  initialChecklist: '',
  isBacklog: false,
  estimateMinutes: '',
  requestedById: '',
  requestedByName: '',
  reminderMinutes: '',
  // Проект-доска и наблюдатели (задача #401). projectAuto — проект подставила
  // сама форма, а не человек: такой при следующем открытии можно заменить.
  projectId: '',
  projectAuto: false,
  watcherIds: [],
  // Раздел доски («регулируемый набор разделов»): '' — без раздела.
  sectionId: '',
};

export { TASK_MAX_ASSIGNEES, isTaskAssignee, taskAssignees } from './taskAssignees';

/* Список исполнителей для формы: тот же состав, но с запасным вариантом на
   случай пустой задачи (создание «себе» из закреплённой карточки). */
const formAssigneeIds = (task, fallbackAssignedTo = '') => {
  const ids = taskAssigneeIds(task);
  if (ids.length) return ids;
  return fallbackAssignedTo ? [String(fallbackAssignedTo)] : [];
};

const buildEmptyTaskForm = (overrides = {}) => ({
  ...EMPTY_TASK_FORM,
  checklistItems: [createEmptyChecklistDraftItem()],
  // Бриф продакшна держим и у задач других типов: передумали тип и вернулись —
  // заполненное на месте. Уходит он только у продакшна (buildTaskJsonPayload).
  productionBrief: emptyProductionBrief(),
  ...overrides,
});

const taskToTaskForm = (task, fallbackAssignedTo = '') => {
  /* Повторяющаяся задача: её собственный срок давно позади, а важен срок
     повторов — сервер ставит каждому «начало + deadline_duration_minutes».
     Поэтому в форме — срок ближайшего повтора. */
  const repeatFrom = task?.recurrence_type ? toDatetimeLocalValue(task?.recurrence_next_at) : '';
  const repeatMinutes = Number(task?.deadline_duration_minutes) || 0;
  const dueAt = repeatFrom
    ? (repeatMinutes > 0 ? shiftLocalDateTime(repeatFrom, repeatMinutes) : '')
    : toDatetimeLocalValue(task?.due_at);
  const reminderMinutes = task?.reminder_minutes_before ? String(task.reminder_minutes_before) : '';
  const form = buildEmptyTaskForm({
    subject: task?.subject || '',
    description: task?.description || '',
    tag: task?.tag || 'task',
    priority: task?.priority || 'normal',
    assigneeIds: formAssigneeIds(task, fallbackAssignedTo),
    dueAt,
    initialDueAt: dueAt,
    initialReminderMinutes: reminderMinutes,
    repeatFrom,
    regulationParentId: task?.regulation_parent_id ? String(task.regulation_parent_id) : '',
    isRegulation: Boolean(task?.is_regulation || task?.recurrence_type),
    recurrenceType: task?.recurrence_type || '',
    recurrenceInterval: String(task?.recurrence_interval || '1'),
    checklistItems: checklistToFormItems(task?.checklist),
    /* null — чек-лист задачи неизвестен: слепок закреплённой задачи, записанный
       прежней версией (без чек-листа), до первой синхронизации. Пустой массив —
       пунктов правда нет. */
    initialChecklist: Array.isArray(task?.checklist) ? checklistKey(task.checklist) : null,
    isBacklog: Boolean(task?.is_backlog),
    estimateMinutes: task?.estimate_minutes ? String(task.estimate_minutes) : '',
    requestedById: task?.requested_by?.id ? String(task.requested_by.id) : '',
    requestedByName: task?.requested_by?.id ? '' : (task?.requested_by?.name || ''),
    reminderMinutes,
    productionBrief: briefFromTask(task?.production_brief),
    projectId: task?.project?.id ? String(task.project.id) : '',
    watcherIds: (Array.isArray(task?.watchers) ? task.watchers : []).map((person) => String(person.id)),
    // С чем задача пришла в правку: проект и наблюдатели уходят на сервер,
    // только если их поменяли (buildTaskJsonPayload).
    initialProjectId: task?.project?.id ? String(task.project.id) : '',
    initialWatcherIds: (Array.isArray(task?.watchers) ? task.watchers : []).map((person) => String(person.id)),
    sectionId: task?.section?.id ? String(task.section.id) : '',
    initialSectionId: task?.section?.id ? String(task.section.id) : '',
  });
  // Повтор уходит на сервер, только если его поменяли (recurrencePayload).
  return { ...form, initialRecurrence: recurrenceKey(form) };
};

const sameIdSet = (left, right) => {
  const a = new Set((left || []).map(String));
  const b = new Set((right || []).map(String));
  return a.size === b.size && [...a].every((id) => b.has(id));
};

const formChecklistItems = (items) => normalizeChecklistItems(items);

/* Чек-лист уходит, только когда его меняли: у новой задачи — всегда (исходного
   нет), в правке — против того, с чем задача пришла (HR-2, 24.11.2026). Прежняя
   форма слала его при каждой правке, сервер пересоздавал пункты, и добавленный
   исполнитель или поправленное описание снимали все отметки. Чек-лист задачи
   неизвестен (initialChecklist null — слепок без него) — не уходит вовсе: сервер
   принял бы присланное за весь чек-лист и убрал бы пункты, которых форма не знала. */
const checklistPayload = (values) => {
  if (values?.initialChecklist === null) return {};
  return values?.initialChecklist && values.initialChecklist === checklistKey(values.checklistItems)
    ? {}
    : { checklist_items: formChecklistItems(values.checklistItems) };
};

const numberFieldValue = (value) => {
  const num = Number(value);
  return Number.isFinite(num) && num > 0 ? String(Math.floor(num)) : '0';
};

/* Срок поменяли — против того, с чем задача пришла в правку (у новой — пусто). */
const deadlineChangedInForm = (values) => String(values?.dueAt || '') !== String(values?.initialDueAt || '');

/* Начало ближайшего повтора — пока задача в форме остаётся повторяющейся. Сняли
   «Повтор» — срок снова срок самой задачи. */
const repeatAnchorOf = (values) => (values?.isRegulation && values?.repeatFrom ? String(values.repeatFrom) : '');

/* Дедлайн уходит днём и временем (`due_at`), а не «через N дней, часов и минут»,
   и только когда его правда меняли. Прежняя форма слала «через N» при каждой
   правке, и сервер отсчитывал его от минуты правки: поправили описание на третий
   день — срок молча уехал на три дня вперёд. А быстрая правка закреплённой задачи
   собирает форму из слепка без срока — «пусто» в нём значит «не знаю», а не
   «снять срок». То же с напоминанием: без срока оно не уходит вовсе.
   У повторяющейся задачи срок — смещение от начала повтора: уходит «через N»
   от начала ближайшего повтора. Дата ушла бы смещением от создания шаблона —
   у шаблона месячной давности каждый повтор получил бы срок через месяц. */
const deadlinePayload = (values) => {
  const dueAt = String(values?.dueAt || '');
  const reminder = dueAt ? (normalizeReminderMinutes(values?.reminderMinutes) || 0) : 0;
  const payload = {};
  if (deadlineChangedInForm(values)) {
    const repeatFrom = repeatAnchorOf(values);
    if (repeatFrom) Object.assign(payload, deadlineDurationFields(dueAt ? minutesBetweenLocal(repeatFrom, dueAt) : 0));
    else payload.due_at = dueAt || null;
  }
  const deadlineSent = 'due_at' in payload || 'deadline_days' in payload;
  if (deadlineSent || reminder !== (normalizeReminderMinutes(values?.initialReminderMinutes) || 0)) {
    payload.reminder_minutes_before = reminder;
  }
  return payload;
};

/* Повтор — как его видит сервер: регламентная ли задача, период и шаг. */
const recurrenceFields = (values) => (values?.isRegulation
  ? {
    is_regulation: true,
    recurrence_type: values.recurrenceType || 'daily',
    recurrence_interval: numberFieldValue(values.recurrenceInterval || '1'),
  }
  : { is_regulation: false, recurrence_type: '', recurrence_interval: '1' });

const recurrenceKey = (values) => JSON.stringify(recurrenceFields(values));

/* Повтор уходит, только когда его меняли: у новой задачи — всегда (исходного нет),
   в правке — против того, с чем задача пришла. Прежняя форма слала его всегда, и
   правка одной темы у повтора регламента (итерации: «регламентная» без периода)
   уходила «ежедневно» — повтор становился вторым шаблоном; а быстрая правка
   закреплённой задачи из слепка без повтора выключала повтор у шаблона. */
const recurrencePayload = (values) => (
  values?.initialRecurrence && values.initialRecurrence === recurrenceKey(values) ? {} : recurrenceFields(values)
);

const DEADLINE_PAST_ISSUE = 'Срок уже прошёл — выберите время позже';

/* Срок в прошлом: задача была бы просрочена с первой минуты. Проверяем только
   изменённый срок — правка задачи, срок которой уже прошёл, сама по себе не
   должна упираться в него. У повторяющейся — срок повтора не раньше его начала. */
const deadlineIssues = (values, now = new Date()) => {
  if (!values?.dueAt || !deadlineChangedInForm(values)) return [];
  const repeatFrom = repeatAnchorOf(values);
  if (repeatFrom) {
    const offset = minutesBetweenLocal(repeatFrom, values.dueAt);
    return offset > 0 ? [] : [{
      key: 'deadline',
      text: `Срок повтора — позже его начала: ${deadlineSummary(repeatFrom, now).toLowerCase()}`,
    }];
  }
  return isLocalDateTimePast(values.dueAt, now) ? [{ key: 'deadline', text: DEADLINE_PAST_ISSUE }] : [];
};

const buildTaskJsonPayload = (values) => ({
  subject: String(values.subject || '').trim(),
  description: String(values.description || '').trim(),
  tag: values.tag || 'task',
  priority: values.priority || 'normal',
  // Первый исполнитель уходит и отдельным полем: у сервера это tasks.assigned_to.
  assigned_to: Number((values.assigneeIds || [])[0] || 0),
  assignee_ids: (values.assigneeIds || []).map(Number).filter((id) => id > 0),
  ...deadlinePayload(values),
  ...recurrencePayload(values),
  ...checklistPayload(values),
  estimate_minutes: numberFieldValue(values.estimateMinutes),
  // Пустые оба поля = своя инициатива; id имеет приоритет над свободным текстом.
  requested_by_id: values.requestedById ? String(values.requestedById) : '',
  requested_by_name: values.requestedById ? '' : String(values.requestedByName || '').trim(),
  // null у задачи другого типа — сервер уберёт бриф, если тип сменили с продакшна.
  production_brief: values.tag === PRODUCTION_TAG ? briefToPayload(values.productionBrief) : null,
  /* Проект и наблюдатели уходят, только когда их правда меняли. Быстрая правка
     закреплённой задачи собирает форму из слепка без проекта: отправь она
     project_id всегда — правка одной темы вынимала бы задачу с доски. */
  ...(String(values.projectId || '') !== String(values.initialProjectId || '') && Number(values.projectId) > 0
    ? { project_id: Number(values.projectId) }
    : {}),
  ...(Number(values.projectId) > 0 && !sameIdSet(values.watcherIds, values.initialWatcherIds)
    ? { watcher_ids: (values.watcherIds || []).map(Number).filter((id) => id > 0) }
    : {}),
  // Раздел — по тому же правилу; «Без раздела» в правке — законный выбор (null).
  ...(String(values.sectionId || '') !== String(values.initialSectionId || '')
    ? { section_id: Number(values.sectionId) > 0 ? Number(values.sectionId) : null }
    : {}),
});

const appendTaskFormData = (body, values) => {
  const payload = buildTaskJsonPayload(values);
  body.append('subject', payload.subject);
  body.append('description', payload.description);
  body.append('tag', payload.tag);
  body.append('priority', payload.priority);
  body.append('assigned_to', String(payload.assigned_to || ''));
  body.append('assignee_ids', payload.assignee_ids.join(','));
  if (payload.due_at) body.append('due_at', payload.due_at);
  if ('is_regulation' in payload) {
    body.append('is_regulation', payload.is_regulation ? '1' : '0');
    body.append('recurrence_type', payload.recurrence_type);
    body.append('recurrence_interval', payload.recurrence_interval);
  }
  if (payload.checklist_items) body.append('checklist_items', JSON.stringify(payload.checklist_items));
  body.append('estimate_minutes', payload.estimate_minutes);
  body.append('is_backlog', values.isBacklog ? '1' : '0');
  body.append('requested_by_id', payload.requested_by_id);
  body.append('requested_by_name', payload.requested_by_name);
  if (payload.reminder_minutes_before) body.append('reminder_minutes_before', String(payload.reminder_minutes_before));
  // Форма multipart, вложенный объект в ней иначе не передать — JSON-строкой.
  if (payload.production_brief) body.append('production_brief', JSON.stringify(payload.production_brief));
  if (payload.project_id) body.append('project_id', String(payload.project_id));
  if (payload.watcher_ids?.length) body.append('watcher_ids', payload.watcher_ids.join(','));
  if (payload.section_id) body.append('section_id', String(payload.section_id));
};

const filesFromList = (files) => Array.from(files || []).filter(Boolean);

/* Пределы сервера (TASK_MAX_FILES / TASK_MAX_FILE_SIZE_BYTES). Проверяем заранее:
   иначе человек ждёт загрузку, чтобы получить отказ, да ещё и по-английски. */
const TASK_UPLOAD_MAX_FILES = 10;
const TASK_UPLOAD_MAX_BYTES = 10 * 1024 * 1024;

const taskUploadRefusal = (files) => {
  if (files.length > TASK_UPLOAD_MAX_FILES) {
    return `За раз можно приложить не больше ${TASK_UPLOAD_MAX_FILES} файлов`;
  }
  const tooBig = files.find((file) => Number(file?.size || 0) > TASK_UPLOAD_MAX_BYTES);
  return tooBig ? `Файл «${tooBig.name}» больше 10 МБ` : '';
};

const fileIdentity = (file) => [
  file?.name || '',
  file?.size || 0,
  file?.lastModified || 0,
].join(':');

const mergeSelectedFiles = (currentFiles, incomingFiles) => {
  const seen = new Set();
  return [...filesFromList(currentFiles), ...filesFromList(incomingFiles)].filter((file) => {
    const key = fileIdentity(file);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

const formatFileSize = (size) => {
  const bytes = Number(size || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 KB';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
};

const pluralRu = (n, one, few, many) => {
  const absN = Math.abs(Number(n) || 0);
  const n10 = absN % 10;
  const n100 = absN % 100;
  if (n10 === 1 && n100 !== 11) return one;
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
  return many;
};

const taskDateKey = (v) => {
  if (!v) return 'unknown';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return 'unknown';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

const taskDateLabel = (v) => {
  if (!v) return 'Без даты';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return 'Без даты';
  const now = new Date();
  let diffMs = now.getTime() - d.getTime();
  if (diffMs < 0) diffMs = 0;

  const diffDays = Math.floor(diffMs / 86400000);
  if (diffDays <= 0) return 'Сегодня';
  if (diffDays < 7) {
    return `${diffDays} ${pluralRu(diffDays, 'день', 'дня', 'дней')} назад`;
  }
  if (diffDays < 30) {
    const weeks = Math.max(1, Math.floor(diffDays / 7));
    return `${weeks} ${pluralRu(weeks, 'неделю', 'недели', 'недель')} назад`;
  }

  const rawMonths =
    (now.getFullYear() - d.getFullYear()) * 12 +
    (now.getMonth() - d.getMonth()) -
    (now.getDate() < d.getDate() ? 1 : 0);
  const months = Math.max(1, rawMonths);
  if (months < 12) {
    return `${months} ${pluralRu(months, 'месяц', 'месяца', 'месяцев')} назад`;
  }

  const years = Math.max(1, Math.floor(months / 12));
  return `${years} ${pluralRu(years, 'год', 'года', 'лет')} назад`;
};

const withDateSeparators = (list) => {
  const safeList = Array.isArray(list) ? list : [];
  const rows = [];
  let prevDateKey = '';
  safeList.forEach((task, idx) => {
    const curDateKey = taskDateKey(task?.created_at);
    if (curDateKey !== prevDateKey) {
      rows.push({
        type: 'separator',
        key: `sep:${curDateKey}:${task?.id ?? idx}`,
        label: taskDateLabel(task?.created_at)
      });
      prevDateKey = curDateKey;
    }
    rows.push({
      type: 'task',
      key: `task:${task?.id ?? idx}`,
      task
    });
  });
  return rows;
};

const initials = (name = '') => {
  const parts = (name || '').trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (parts[0]?.[0] || '?').toUpperCase();
};

/* getPeople может вернуть и одного человека, и список. Задача с несколькими
   исполнителями попадает в группу КАЖДОГО из них: иначе соисполнитель не нашёл
   бы её у себя, а в этом и весь смысл нескольких исполнителей. */
const groupTasksByPerson = (list, getPeople) => {
  const groups = new Map();
  list.forEach((task) => {
    const picked = getPeople(task);
    const people = (Array.isArray(picked) ? picked : [picked]).filter(Boolean);
    /* В «Моих задачах» — только те, кому раздел «Задачи» открыт, и не уволенные
       (владелец, 17.11): человек бывает в чужих задачах, сам в раздел не попадая.
       Их задачи остаются во «Всех задачах». Признака доступа нет (сервер не узнал)
       — не прячем. Задача, где спрятаны все, в людях не появляется и не падает в
       группу «без имени». */
    const visible = people.filter((person) => !person?.is_fired && person?.has_tasks_access !== false);
    if (people.length && !visible.length) return;
    (visible.length ? visible : [{}]).forEach((person) => {
    const idNum = Number(person?.id || 0);
    const name = (person?.name || '-').trim() || '-';
    const avatarUrl = (person?.avatar_url || '').trim();
    const key = idNum > 0 ? `id:${idNum}` : `name:${name}`;
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        personId: idNum > 0 ? idNum : null,
        name,
        avatarUrl,
        done: 0,
        active: 0,
        notAccepted: 0,
        tasks: []
      });
    }
    const group = groups.get(key);
    if (!group.avatarUrl && avatarUrl) group.avatarUrl = avatarUrl;
    group.tasks.push(task);
    if (DONE_STATUSES.has(task?.status)) group.done += 1;
    else if (NOT_ACCEPTED_STATUSES.has(task?.status)) group.notAccepted += 1;
    else group.active += 1;
    });
  });
  return Array.from(groups.values()).sort((a, b) =>
    a.name.localeCompare(b.name, 'ru-RU', { sensitivity: 'base' })
  );
};

/* ─── Icons ─── */
const CloseIcon = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
    <path d="M1 1l12 12M13 1L1 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
  </svg>
);
const FileIcon = () => (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
    <path d="M9 1H3a1 1 0 00-1 1v12a1 1 0 001 1h10a1 1 0 001-1V6L9 1z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>
    <path d="M9 1v5h5" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>
  </svg>
);
/* Файлы одного блока карточки: картинки — плитками, как вложения в «Сообщениях»
   (щелчок открывает снимок во весь экран), остальное — кнопками с именем файла.
   Кнопка открывает файл в окне просмотра (onOpenFile → FilePreviewSheet): его
   читают, не скачивая (просьба владельца 13.11.2026); листается окно внутри
   этого же блока. Без onOpenFile — прежнее скачивание. Картинка, для которой
   адреса нет, остаётся кнопкой (splitTaskFiles).
   Имени у плитки нет намеренно: «photo.webp» под каждой картинкой — шум, а
   имя файла видно в просмотре по кнопке «Скачать». */
const TaskFileGroup = ({ attachments, photoState, onOpenPhoto, onOpenFile, onDownload, fileBtnStyle }) => {
  const { photos, files } = splitTaskFiles(
    attachments,
    photoState?.previews,
    photoState ? photoState.settled : true,
    photoState?.broken
  );
  const openable = photos.filter((item) => item.preview).map((item) => item.attachment);
  return (
    <>
      {photos.length > 0 && (
        <div className="tv-photo-grid">
          {photos.map(({ attachment, preview }) => (
            <button
              key={attachment.id}
              type="button"
              className="tv-photo-tile"
              disabled={!preview}
              aria-label={preview ? 'Открыть фотографию' : 'Фотография загружается'}
              onClick={() => onOpenPhoto?.(openable, openable.indexOf(attachment))}
            >
              {preview && (
                <img
                  src={preview.thumbUrl}
                  alt=""
                  loading="lazy"
                  decoding="async"
                  draggable={false}
                  onError={() => photoState?.markBroken?.(attachment.id)}
                />
              )}
            </button>
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="tv-file-list">
          {files.map((att) => (
            <button key={att.id} type="button" className="tv-file-btn" style={fileBtnStyle}
              title={att.file_name}
              onClick={() => (onOpenFile ? onOpenFile(files, files.indexOf(att)) : onDownload?.(att))}>
              <FileIcon /><span className="tv-file-name">{att.file_name}</span>
            </button>
          ))}
        </div>
      )}
    </>
  );
};
const RESULT_FILE_BTN_STYLE = { background: '#e0e7ff', borderColor: '#c7d2fe', color: '#3730a3' };
/* Ключ файла задачи в памяти просмотра: тот же файл из карточки, из виджета и
   по «Скачать» берётся без второго запроса. */
const taskFileKey = (att) => `task-attachment:${Number(att?.id || 0)}`;
const ChevronRight = () => (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
    <path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);
const BackIcon = () => (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
    <path d="M10 12l-4-4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);
/* Шеврон «Назад» в шапке экрана на телефоне — тот же, что у IosModal. */
const ScreenBackChevron = () => (
  <svg width="17" height="17" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M10 2.5L4.5 8l5.5 5.5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/* Уход с окна. На компьютере — крестик в правом углу, на телефоне окно — это
   экран, и уходят с него шевроном слева: крестик справа читается как
   «отменить», а двух «закрыть» в одной шапке быть не должно (решение
   владельца 10.09.2026, см. IosModal). */
const ModalCloseButton = ({ isMobileShell, onClick, disabled = false }) => (isMobileShell ? (
  <button type="button" className="otp-modal-back tv-screen-back" aria-label="Назад" onClick={onClick} disabled={disabled}>
    <ScreenBackChevron />
  </button>
) : (
  <button type="button" className="tv-close-btn" onClick={onClick} disabled={disabled}>
    <CloseIcon />
  </button>
));

const PlusIcon = () => (
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
    <path d="M8 2v12M2 8h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
  </svg>
);
const SearchIcon = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
    <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.5"/>
    <path d="M10.5 10.5l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
  </svg>
);

const AvatarCircle = ({ className, name, avatarUrl }) => (
  <span className={className}>
    {avatarUrl
      ? <img className="tv-avatar-media" src={avatarUrl} alt={name || 'avatar'} loading="lazy" />
      : initials(name)}
  </span>
);

/* Фильтр «Все задачи» на компьютере — кнопка-меню, как в «Почте» и «Напоминаниях»:
   одна кнопка на признак вместо 11 капсул в ряд (владелец выбрал 16.11). Выбранное
   видно в подписи кнопки и мягким голубым; «Все …» снимает фильтр. */
const TaskFilterMenu = ({ title, allLabel, value, options, onChange }) => {
  const current = options.find((option) => option.value === value) || null;
  const caption = current ? `${title}: ${current.label}` : title;
  return (
    <IosMenu
      label={current ? caption : `${title}: все`}
      align="left"
      trigger={{
        className: `tv-filter-menu ${current ? 'is-set' : ''}`,
        text: (
          <>
            <span>{caption}</span>
            <ChevronDown size={15} strokeWidth={2.2} aria-hidden="true" />
          </>
        ),
      }}
      items={[
        { key: '__all', label: allLabel, checked: !current, onSelect: () => onChange('') },
        ...options.map((option, index) => ({
          key: option.value,
          label: option.label,
          checked: option.value === value,
          separatorBefore: index === 0,
          onSelect: () => onChange(option.value),
        })),
      ]}
    />
  );
};

/* Подсказка у аватара в «Моих задачах» — по центру, а у краёв окна — к краю:
   у левого она уходила бы под меню портала, у правого — за окно. */
const alignPeopleTip = (event) => {
  const node = event.currentTarget;
  const rect = node.getBoundingClientRect();
  const width = (node.ownerDocument?.defaultView || window).innerWidth;
  node.dataset.tip = rect.left < 240 ? 'start' : (width - rect.right < 140 ? 'end' : 'center');
};

/* Исполнители на карточке. Один — как было: аватар и имя. Несколько — стопка
   лиц без имён: три имени в строку не влезают, а обрезанные «Айгуль…, Серг…»
   читаются хуже, чем аватары. Полный список — в подсказке и для скринридера. */
const AssigneeStack = React.memo(({ people, max = 3 }) => {
  const list = Array.isArray(people) ? people.filter(Boolean) : [];
  const names = list.length ? list.map((person) => person?.name || '—').join(', ') : '—';
  if (list.length < 2) {
    return (
      <span className="tv-task-row-assignee-chip" title={names}>
        <AvatarCircle className="tv-avatar-xs" name={names} avatarUrl={list[0]?.avatar_url || ''} />
        <span className="tv-task-row-assignee-name">{names}</span>
      </span>
    );
  }
  const shown = list.slice(0, max);
  const rest = list.length - shown.length;
  return (
    <span className="tv-task-row-assignee-chip is-stack" title={names} aria-label={`Исполнители: ${names}`}>
      <span className="tv-avatar-stack">
        {shown.map((person, index) => (
          <AvatarCircle
            key={person?.id ?? index}
            className="tv-avatar-xs tv-avatar-stacked"
            name={person?.name || '—'}
            avatarUrl={person?.avatar_url || ''}
          />
        ))}
        {rest > 0 && <span className="tv-avatar-xs tv-avatar-stacked tv-avatar-rest">+{rest}</span>}
      </span>
    </span>
  );
});

/* Поле «Исполнители». Пока выбран один человек, выглядит и ведёт себя точно как
   прежний одиночный селектор — на экране не появляется ни одного нового
   элемента. Со второго человека в кнопке встаёт стопка лиц и «и ещё N». */
const AssigneeField = ({
  id,
  value,
  options,
  people = [],
  onChange,
  disabled = false,
  isLoading = false,
  ariaLabel = 'Исполнители',
  placeholder = 'Выберите сотрудника',
  maxSelected = TASK_MAX_ASSIGNEES,
}) => {
  const byId = useMemo(() => {
    const map = new Map();
    (people || []).forEach((person) => {
      if (Number(person?.id || 0) > 0) map.set(String(person.id), person);
    });
    return map;
  }, [people]);

  /* Подпись одного выбранного берём ИЗ ОПЦИИ, а не из имени человека: в опции
     рядом с именем стоит роль («Айгуль (Супервайзер)»), и одиночный селектор
     показывал именно её — терять её при переходе на мультивыбор незачем. */
  const labelById = useMemo(() => {
    const map = new Map();
    (options || []).forEach((option) => map.set(String(option.value), option.label));
    return map;
  }, [options]);

  const renderValue = useCallback((ids) => {
    const picked = ids.map((personId) => byId.get(String(personId))).filter(Boolean);
    if (!picked.length) return `Выбрано: ${ids.length}`;
    if (picked.length === 1) return labelById.get(String(picked[0].id)) || picked[0].name;
    return (
      <span className="tv-assignee-value">
        <span className="tv-avatar-stack">
          {picked.slice(0, 3).map((person) => (
            <AvatarCircle
              key={person.id}
              className="tv-avatar-xs tv-avatar-stacked"
              name={person.name}
              avatarUrl={person.avatar_url || ''}
            />
          ))}
        </span>
        <span className="tv-assignee-value-text">
          {picked[0].name} и ещё {picked.length - 1}
        </span>
      </span>
    );
  }, [byId, labelById]);

  return (
    <CustomSelect
      id={id}
      className="tv-person-select"
      variant="ios"
      ariaLabel={ariaLabel}
      multiple
      maxSelected={maxSelected}
      value={value}
      options={options}
      onChange={(next) => onChange(Array.isArray(next) ? next : [])}
      disabled={disabled || isLoading}
      placeholder={isLoading ? 'Загрузка...' : placeholder}
      renderValue={renderValue}
      searchable
      searchPlaceholder="Поиск по имени…"
    />
  );
};

/* «Все» в форме: кому именно из исполнителей доски уйдёт задача — лица и имя первого,
   весь список — в подсказке. Это не поле выбора: состав держит сама форма, а
   поменять его — сегментом «Конкретные люди». Высотой с селектор рядом, чтобы
   строка не прыгала при переключении. */
const AllAssigneesSummary = ({ people = [] }) => {
  const list = (people || []).filter(Boolean);
  const names = list.map((person) => person?.name || '—').join(', ');
  const shown = list.slice(0, 4);
  const rest = list.length - shown.length;
  return (
    <div className="tv-assignee-all" title={names} aria-label={`Исполнители: ${names}`}>
      <span className="tv-avatar-stack" aria-hidden="true">
        {shown.map((person, index) => (
          <AvatarCircle
            key={person?.id ?? index}
            className="tv-avatar-xs tv-avatar-stacked"
            name={person?.name || '—'}
            avatarUrl={person?.avatar_url || ''}
          />
        ))}
        {rest > 0 && <span className="tv-avatar-xs tv-avatar-stacked tv-avatar-rest">+{rest}</span>}
      </span>
      <span className="tv-assignee-value-text">
        {list.length > 1 ? `${list[0]?.name || '—'} и ещё ${list.length - 1}` : (list[0]?.name || '—')}
      </span>
    </div>
  );
};

const TaskFileDropzone = React.memo(({
  files = [],
  disabled = false,
  onChange,
  title = 'Вложения',
  prompt = 'Перетащите файлы сюда',
  hint = 'или нажмите, чтобы выбрать',
}) => {
  const inputRef = useRef(null);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  const selectedFiles = useMemo(() => filesFromList(files), [files]);

  const addFiles = useCallback((nextFiles) => {
    const incomingFiles = filesFromList(nextFiles);
    if (!incomingFiles.length) return;
    onChange?.(mergeSelectedFiles(selectedFiles, incomingFiles));
  }, [onChange, selectedFiles]);

  const removeFile = useCallback((index) => {
    onChange?.(selectedFiles.filter((_, fileIndex) => fileIndex !== index));
  }, [onChange, selectedFiles]);

  const openFilePicker = useCallback(() => {
    if (disabled) return;
    inputRef.current?.click?.();
  }, [disabled]);

  const handleKeyDown = useCallback((event) => {
    if (disabled) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    openFilePicker();
  }, [disabled, openFilePicker]);

  const handleDragOver = useCallback((event) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = disabled ? 'none' : 'copy';
    if (!disabled) setIsDraggingFile(true);
  }, [disabled]);

  const handleDragLeave = useCallback((event) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget && event.currentTarget.contains(nextTarget)) return;
    setIsDraggingFile(false);
  }, []);

  const handleDrop = useCallback((event) => {
    event.preventDefault();
    setIsDraggingFile(false);
    if (disabled) return;
    addFiles(event.dataTransfer?.files);
  }, [addFiles, disabled]);

  return (
    <div className="tv-file-dropzone-wrap">
      <div
        className={`tv-file-dropzone ${isDraggingFile ? 'is-dragging' : ''} ${selectedFiles.length ? 'has-files' : ''} ${disabled ? 'is-disabled' : ''}`}
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        onClick={openFilePicker}
        onKeyDown={handleKeyDown}
        onDragEnter={handleDragOver}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="tv-file-dropzone-input"
          disabled={disabled}
          onChange={(event) => {
            addFiles(event.target.files);
            event.target.value = '';
          }}
        />
        <span className="tv-file-dropzone-icon"><FileIcon /></span>
        <span className="tv-file-dropzone-copy">
          <strong>{prompt}</strong>
          <span>{hint}</span>
        </span>
        {selectedFiles.length > 0 && (
          <span className="tv-file-dropzone-count">
            {selectedFiles.length} {pluralRu(selectedFiles.length, 'файл', 'файла', 'файлов')}
          </span>
        )}
      </div>
      {selectedFiles.length > 0 && (
        <div className="tv-selected-file-list" aria-label={title}>
          {selectedFiles.map((file, index) => (
            <div className="tv-selected-file-pill" key={`${fileIdentity(file)}:${index}`}>
              <FileIcon />
              <span className="tv-selected-file-text">
                <strong title={file.name}>{file.name}</strong>
                <span>{formatFileSize(file.size)} · <span className="tv-file-mark">новый файл</span></span>
              </span>
              <button
                type="button"
                className="tv-file-remove-btn"
                disabled={disabled}
                aria-label={`Убрать файл ${file.name}`}
                onClick={() => removeFile(index)}
              >
                <CloseIcon />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

/* ─── TaskRow — defined outside to avoid remount ─── */
const useTaskNotes = ({ userId, apiBaseUrl = '', buildHeaders, notify } = {}) => {
  const normalizedUserId = Number(userId || 0);
  const [notes, setNotes] = useState(() => readLocalNotes(normalizedUserId));
  const [activeNoteId, setActiveNoteId] = useState('');
  const [draft, setDraft] = useState(EMPTY_TASK_NOTE);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [backendReady, setBackendReady] = useState(true);

  const applyNotes = useCallback((nextNotes, preferredId = '') => {
    const normalized = normalizeTaskNotesList(nextNotes);
    setNotes(normalized);
    setActiveNoteId((prev) => {
      const preferred = preferredId || prev;
      if (preferred && normalized.some((note) => String(note.id) === String(preferred))) return String(preferred);
      return normalized[0]?.id || '';
    });
  }, []);

  const resolveHeaders = useCallback(() => {
    const base = normalizedUserId ? { 'X-User-Id': String(normalizedUserId) } : {};
    return typeof buildHeaders === 'function' ? buildHeaders(base) : base;
  }, [buildHeaders, normalizedUserId]);

  const syncNotes = useCallback((nextNotes, preferredId = '') => {
    const normalized = normalizeTaskNotesList(nextNotes);
    writeLocalNotes(normalizedUserId, normalized);
    applyNotes(normalized, preferredId);
  }, [applyNotes, normalizedUserId]);

  const fetchNotes = useCallback(async () => {
    if (!normalizedUserId) {
      applyNotes([]);
      return;
    }
    setIsLoading(true);
    try {
      const res = await axios.get(`${apiBaseUrl}/api/tasks/notes`, { headers: resolveHeaders() });
      const list = Array.isArray(res?.data?.notes) ? res.data.notes : [];
      setBackendReady(true);
      syncNotes(list);
    } catch (error) {
      setBackendReady(false);
      applyNotes(readLocalNotes(normalizedUserId));
    } finally {
      setIsLoading(false);
    }
  }, [apiBaseUrl, applyNotes, normalizedUserId, resolveHeaders, syncNotes]);

  useEffect(() => {
    fetchNotes();
  }, [fetchNotes]);

  useEffect(() => {
    const active = notes.find((note) => String(note.id) === String(activeNoteId)) || null;
    setDraft(active || EMPTY_TASK_NOTE);
  }, [activeNoteId, notes]);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const handleNotesChange = (event) => {
      const eventUserId = Number(event?.detail?.userId || 0);
      if (eventUserId !== normalizedUserId) return;
      applyNotes(event?.detail?.notes || readLocalNotes(normalizedUserId), activeNoteId);
    };
    const handleStorage = (event) => {
      if (event.key !== localNotesKey(normalizedUserId)) return;
      applyNotes(readLocalNotes(normalizedUserId), activeNoteId);
    };
    window.addEventListener(LOCAL_NOTES_EVENT, handleNotesChange);
    window.addEventListener('storage', handleStorage);
    return () => {
      window.removeEventListener(LOCAL_NOTES_EVENT, handleNotesChange);
      window.removeEventListener('storage', handleStorage);
    };
  }, [activeNoteId, applyNotes, normalizedUserId]);

  const createNote = useCallback((overrides = {}) => {
    const nextNote = normalizeTaskNote({
      ...createEmptyTaskNote(),
      ...overrides,
      id: createDraftNoteId(),
      is_local: true,
    });
    const nextNotes = [nextNote, ...notes];
    syncNotes(nextNotes, nextNote.id);
  }, [notes, syncNotes]);

  const saveNote = useCallback(async () => {
    if (!normalizedUserId) return;
    const base = draft?.id ? normalizeTaskNote(draft) : createEmptyTaskNote();
    const payload = buildNotePayload(base);
    const localSavedAt = new Date().toISOString();
    const localNote = normalizeTaskNote({
      ...base,
      ...payload,
      saved_at: localSavedAt,
      updated_at: localSavedAt,
      is_local: !isPersistedNoteId(base.id),
    });
    const upsertLocal = (saved) => {
      const savedId = String(saved.id);
      const nextNotes = notes.some((note) => String(note.id) === String(base.id))
        ? notes.map((note) => String(note.id) === String(base.id) ? saved : note)
        : [saved, ...notes];
      syncNotes(nextNotes, savedId);
    };

    setIsSaving(true);
    try {
      const res = isPersistedNoteId(base.id)
        ? await axios.patch(`${apiBaseUrl}/api/tasks/notes/${base.id}`, payload, { headers: resolveHeaders() })
        : await axios.post(`${apiBaseUrl}/api/tasks/notes`, payload, { headers: resolveHeaders() });
      const saved = normalizeTaskNote(res?.data?.note || localNote);
      upsertLocal({ ...saved, is_local: false });
      setBackendReady(true);
      notify?.('Заметка сохранена');
    } catch (error) {
      upsertLocal(localNote);
      setBackendReady(false);
      notify?.(error?.response?.data?.error || 'Не удалось сохранить заметку в БД, сохранено локально', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [apiBaseUrl, draft, normalizedUserId, notes, notify, resolveHeaders, syncNotes]);

  const deleteNote = useCallback(async (noteId = activeNoteId) => {
    const normalizedNoteId = String(noteId || '');
    if (!normalizedNoteId) return;
    const removeLocal = () => {
      const nextNotes = notes.filter((note) => String(note.id) !== normalizedNoteId);
      syncNotes(nextNotes);
    };

    if (!isPersistedNoteId(normalizedNoteId)) {
      removeLocal();
      return;
    }

    setIsSaving(true);
    try {
      await axios.delete(`${apiBaseUrl}/api/tasks/notes/${normalizedNoteId}`, { headers: resolveHeaders() });
      removeLocal();
      setBackendReady(true);
      notify?.('Заметка удалена');
    } catch (error) {
      setBackendReady(false);
      notify?.(error?.response?.data?.error || 'Не удалось удалить заметку', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [activeNoteId, apiBaseUrl, notes, notify, resolveHeaders, syncNotes]);

  const toggleNoteDone = useCallback(async (note, isDone) => {
    const base = normalizeTaskNote({ ...note, is_task: true, is_done: Boolean(isDone) });
    const payload = { is_task: true, is_done: Boolean(isDone) };
    const localSavedAt = new Date().toISOString();
    const localNote = normalizeTaskNote({
      ...base,
      ...payload,
      saved_at: localSavedAt,
      updated_at: localSavedAt,
      completed_at: isDone ? (base.completed_at || localSavedAt) : null,
    });
    const nextNotes = notes.map((item) => String(item.id) === String(base.id) ? localNote : item);
    syncNotes(nextNotes, base.id);

    if (!isPersistedNoteId(base.id)) return;

    try {
      const res = await axios.patch(`${apiBaseUrl}/api/tasks/notes/${base.id}`, payload, { headers: resolveHeaders() });
      const saved = normalizeTaskNote(res?.data?.note || localNote);
      syncNotes(notes.map((item) => String(item.id) === String(base.id) ? saved : item), saved.id);
      setBackendReady(true);
    } catch (error) {
      setBackendReady(false);
      notify?.(error?.response?.data?.error || 'Не удалось обновить отметку выполнения', 'error');
    }
  }, [apiBaseUrl, notes, notify, resolveHeaders, syncNotes]);

  return {
    notes,
    activeNoteId,
    draft,
    setDraft,
    selectNote: setActiveNoteId,
    createNote,
    saveNote,
    deleteNote,
    toggleNoteDone,
    refreshNotes: fetchNotes,
    isLoading,
    isSaving,
    backendReady,
  };
};

const TaskNoteCard = React.memo(({ note, active = false, onSelect, onToggleDone }) => {
  const priorityMeta = PRIORITY_META[note?.priority] || PRIORITY_META.normal;
  const dueLabel = noteDeadlineLabel(note);
  return (
    <button
      type="button"
      className={`tv-note-topic-btn ${active ? 'is-active' : ''} ${note?.is_done ? 'is-done' : ''}`}
      onClick={() => onSelect?.(note.id)}
    >
      <span className="tv-note-card-head">
        {note?.is_task ? (
          <span
            className="tv-note-done-toggle"
            role="checkbox"
            aria-checked={Boolean(note.is_done)}
            title={note.is_done ? 'Вернуть в работу' : 'Отметить выполненной'}
            onClick={(event) => {
              event.stopPropagation();
              onToggleDone?.(note, !note.is_done);
            }}
          >
            <CheckCircle2 size={14} strokeWidth={2.2} />
          </span>
        ) : (
          <StickyNote size={14} strokeWidth={2} />
        )}
        <span className="tv-note-topic-title">{note?.title || 'Без темы'}</span>
      </span>
      <span className="tv-note-topic-date">
        {dueLabel || (note?.saved_at ? fmtShortDateTime(note.saved_at) : 'Черновик')}
        {/* Напоминание — маленькие часы у срока: видно, что оно стоит, без лишнего текста. */}
        {Boolean(note?.reminder_minutes_before) && !note?.is_done && (
          <span
            className="tv-note-reminder-mark"
            title={`Напоминание в Telegram ${reminderLabel(note.reminder_minutes_before).toLowerCase()} до дедлайна`}
          >
            <CalendarClock size={11} strokeWidth={2} />
          </span>
        )}
      </span>
      <span className="tv-note-card-meta">
        <span className={`tv-badge ${priorityMeta.badge}`}>{priorityMeta.label}</span>
        {note?.is_task && <span className="tv-note-task-mark">Задача</span>}
      </span>
    </button>
  );
});

const TaskNoteColumn = React.memo(({ title, subtitle, notes, activeNoteId, emptyText, onSelect, onToggleDone }) => (
  <section className="tv-note-column">
    <div className="tv-note-column-head">
      <span>
        <strong>{title}</strong>
        <small>{subtitle}</small>
      </span>
      <span className="tv-note-column-count">{notes.length}</span>
    </div>
    <div className="tv-note-column-list">
      {notes.length > 0 ? notes.map((note) => (
        <TaskNoteCard
          key={note.id}
          note={note}
          active={String(note.id) === String(activeNoteId)}
          onSelect={onSelect}
          onToggleDone={onToggleDone}
        />
      )) : (
        <span className="tv-note-column-empty">{emptyText}</span>
      )}
    </div>
  </section>
));

const TaskNotesPanel = React.memo(({ notesState, compact = false, fullScreen = false, wide = false, hideHeading = false }) => {
  const {
    notes,
    activeNoteId,
    draft,
    setDraft,
    selectNote,
    createNote,
    saveNote,
    deleteNote,
    toggleNoteDone,
    refreshNotes,
    isLoading,
    isSaving,
    backendReady,
  } = notesState;
  const ordinaryNotes = useMemo(
    () => notes.filter((note) => !note?.due_at).sort(compareNotesByUpdatedDesc),
    [notes]
  );
  const deadlineNotes = useMemo(
    () => notes.filter((note) => note?.due_at).sort(compareNotesByRelevance),
    [notes]
  );
  const normalizedDraft = draft?.id ? normalizeTaskNote(draft) : EMPTY_TASK_NOTE;
  const hasActiveDraft = Boolean(normalizedDraft?.id);
  const draftDueValue = toDatetimeLocalValue(normalizedDraft.due_at);
  const draftPriority = normalizeTaskNotePriority(normalizedDraft.priority);
  const draftPriorityMeta = PRIORITY_META[draftPriority] || PRIORITY_META.normal;
  const hasDraftDetails = Boolean(
    normalizedDraft.due_at ||
    normalizedDraft.reminder_minutes_before ||
    normalizedDraft.is_task ||
    normalizedDraft.is_done ||
    draftPriority !== 'normal'
  );
  const [noteDetailsOpen, setNoteDetailsOpen] = useState(false);

  useEffect(() => {
    setNoteDetailsOpen(hasDraftDetails);
  }, [normalizedDraft.id, hasDraftDetails]);

  const noteDetailsSummary = useMemo(() => {
    const parts = [];
    if (normalizedDraft.due_at) parts.push('дедлайн');
    if (normalizedDraft.reminder_minutes_before) {
      parts.push(reminderLabel(normalizedDraft.reminder_minutes_before).toLowerCase());
    }
    if (draftPriority !== 'normal') parts.push(draftPriorityMeta.label.toLowerCase());
    if (normalizedDraft.is_task) parts.push(normalizedDraft.is_done ? 'выполнено' : 'как задача');
    return parts.length ? parts.join(' · ') : 'не заданы';
  }, [draftPriority, draftPriorityMeta.label, normalizedDraft.due_at, normalizedDraft.is_done,
      normalizedDraft.is_task, normalizedDraft.reminder_minutes_before]);

  const updateDraft = useCallback((patch) => {
    setDraft((prev) => normalizeTaskNote({ ...normalizeTaskNote(prev), ...patch }));
  }, [setDraft]);

  return (
    <div className={`tv-notes-panel ${compact ? 'is-compact' : ''} ${fullScreen ? 'is-fullscreen' : ''} ${wide ? 'is-wide' : ''}`}>
      <div className="tv-notes-toolbar">
        {/* В широком окне и в закреплённой задаче заголовок и статус уже в шапке
            окна — второй раз не повторяем. */}
        {!wide && !hideHeading && (
          <div>
            <span className="tv-block-label" style={{ margin: 0 }}>Заметки</span>
            <p>{backendReady ? 'Сохраняются в базе данных' : 'Сервер недоступен, включён локальный режим'}</p>
          </div>
        )}
        <div className="tv-notes-toolbar-actions">
          <button type="button" className="tv-btn tv-btn-ghost" onClick={refreshNotes} disabled={isLoading || isSaving}>
            <RefreshCw size={13} strokeWidth={2} />
          </button>
          <button type="button" className="tv-btn tv-btn-primary" onClick={() => createNote()} disabled={isSaving}>
            <PlusIcon /> Новая
          </button>
        </div>
      </div>

      <div className="tv-notes-content">
        <div className="tv-notes-board">
          <TaskNoteColumn
            title="Обычные заметки"
            subtitle="Без дедлайна"
            notes={ordinaryNotes}
            activeNoteId={activeNoteId}
            emptyText="Пока пусто. Добавьте обычную заметку."
            onSelect={selectNote}
            onToggleDone={toggleNoteDone}
          />
          <TaskNoteColumn
            title="С дедлайном"
            subtitle="Сортировка по срочности"
            notes={deadlineNotes}
            activeNoteId={activeNoteId}
            emptyText="Заметки с дедлайном появятся здесь."
            onSelect={selectNote}
            onToggleDone={toggleNoteDone}
          />
        </div>

        <section className={`tv-notes-editor ${normalizedDraft.is_done ? 'is-done' : ''}`}>
          <div className="tv-notes-editor-head">
            <span className="tv-block-label" style={{ margin: 0 }}>Карточка заметки</span>
            {normalizedDraft.due_at && (
              <span className="tv-note-due-pill"><CalendarClock size={13} strokeWidth={2} />{noteDeadlineLabel(normalizedDraft)}</span>
            )}
          </div>
          <input
            className="tv-input"
            value={normalizedDraft.title || ''}
            maxLength={160}
            placeholder="Тема заметки"
            disabled={isSaving}
            onChange={(event) => updateDraft({ title: event.target.value })}
          />
          <textarea
            className="tv-textarea tv-note-textarea"
            value={normalizedDraft.body || ''}
            placeholder="Текст заметки"
            disabled={isSaving}
            onChange={(event) => updateDraft({ body: event.target.value })}
          />
          <button
            type="button"
            className={`tv-note-advanced-toggle ${noteDetailsOpen ? 'is-open' : ''}`}
            aria-expanded={noteDetailsOpen}
            onClick={() => setNoteDetailsOpen((prev) => !prev)}
          >
            <span className="tv-note-advanced-toggle-main">
              <SlidersHorizontal size={14} strokeWidth={2} />
              Детали
            </span>
            <span className="tv-note-advanced-toggle-meta">{noteDetailsSummary}</span>
            <ChevronDown className="tv-note-advanced-caret" size={14} strokeWidth={2} />
          </button>
          {noteDetailsOpen && (
            <div className="tv-note-advanced-panel">
              <div className="tv-note-controls-grid">
            {/* Срок, напоминание и приоритет — пикерами сайта и сегментами, а не
                системными полями: их раскрытые списки и календарь рисует браузер. */}
            <div className="tv-form-field">
              <label>Дедлайн</label>
              <TaskDateTimeField
                value={draftDueValue}
                disabled={isSaving}
                ariaLabel="Дедлайн заметки"
                onChange={(next) => {
                  const nextDue = next || null;
                  updateDraft({
                    due_at: nextDue,
                    // Поставили срок — сразу предлагаем напомнить за день; сняли — напоминание не нужно.
                    reminder_minutes_before: nextDue
                      ? (normalizedDraft.reminder_minutes_before || REMINDER_DEFAULT_MINUTES)
                      : null,
                  });
                }}
              />
            </div>
            <div className="tv-form-field" title={normalizedDraft.due_at ? '' : 'Сначала поставьте дедлайн'}>
              <label>Напомнить в Telegram</label>
              <CustomSelect
                className="tv-person-select"
                variant="ios"
                ariaLabel="Напомнить в Telegram"
                value={String(normalizedDraft.reminder_minutes_before || 0)}
                options={REMINDER_SELECT_OPTIONS}
                disabled={isSaving || !normalizedDraft.due_at}
                onChange={(next) => updateDraft({ reminder_minutes_before: normalizeReminderMinutes(next) })}
              />
            </div>
            <div className="tv-form-field is-wide">
              <label>Приоритет</label>
              <div className="tv-composer-segmented is-stretch" role="group" aria-label="Приоритет заметки">
                {PRIORITY_OPTIONS.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    aria-pressed={(normalizedDraft.priority || 'normal') === item.value}
                    className={(normalizedDraft.priority || 'normal') === item.value ? 'is-active' : ''}
                    disabled={isSaving}
                    onClick={() => updateDraft({ priority: item.value })}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="tv-note-switches">
            <label className="tv-form-switch">
              <input
                type="checkbox"
                checked={Boolean(normalizedDraft.is_task)}
                disabled={isSaving}
                onChange={(event) => updateDraft({
                  is_task: event.target.checked,
                  is_done: event.target.checked ? normalizedDraft.is_done : false,
                })}
              />
              Отметить как задачу
            </label>
            {normalizedDraft.is_task && (
              <label className="tv-form-switch">
                <input
                  type="checkbox"
                  checked={Boolean(normalizedDraft.is_done)}
                  disabled={isSaving}
                  onChange={(event) => updateDraft({ is_done: event.target.checked })}
                />
                Выполнено
              </label>
            )}
          </div>
            </div>
          )}
          <div className="tv-notes-editor-footer">
            <span className="tv-notes-editor-status">
              {isLoading ? 'Загружаю...' : normalizedDraft.saved_at ? `Сохранено: ${fmtShortDateTime(normalizedDraft.saved_at)}` : 'Пока не сохранено'}
            </span>
            <div className="tv-notes-editor-actions">
              {hasActiveDraft && (
                <button type="button" className="tv-btn tv-btn-rose" disabled={isSaving} onClick={() => deleteNote(normalizedDraft.id)}>
                  Удалить
                </button>
              )}
              <button type="button" className="tv-btn tv-btn-amber" disabled={isSaving} onClick={saveNote}>
                {isSaving ? 'Сохраняю...' : 'Сохранить'}
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
});

const ChecklistDraftEditor = React.memo(({ items, disabled = false, onChange, compact = false }) => {
  const list = Array.isArray(items) && items.length ? items : [createEmptyChecklistDraftItem()];
  return (
    <div className={`tv-checklist-editor ${compact ? 'is-compact' : ''}`}>
      {list.map((item, index) => (
        <div className="tv-checklist-editor-row" key={`checklist-draft-${index}`}>
          {compact && (
            <label className="tv-checklist-required" title="Обязательный пункт">
              <input
                type="checkbox"
                checked={item.is_required !== false}
                disabled={disabled}
                onChange={(event) => onChange?.(updateChecklistDraftItem(list, index, { is_required: event.target.checked }))}
              />
              Обязательный
            </label>
          )}
          <input
            className="tv-input"
            value={item.title || ''}
            disabled={disabled}
            placeholder={`Пункт чек-листа ${index + 1}`}
            onChange={(event) => onChange?.(updateChecklistDraftItem(list, index, { title: event.target.value }))}
          />
          {!compact && (
            <label className="tv-checklist-required">
              <input
                type="checkbox"
                checked={item.is_required !== false}
                disabled={disabled}
                onChange={(event) => onChange?.(updateChecklistDraftItem(list, index, { is_required: event.target.checked }))}
              />
              Обязательный
            </label>
          )}
          <button
            type="button"
            className="tv-checklist-editor-remove"
            disabled={disabled}
            title="Удалить пункт"
            aria-label="Удалить пункт"
            onClick={() => onChange?.(removeChecklistDraftItem(list, index))}
          >
            <CloseIcon />
          </button>
        </div>
      ))}
      <button
        type="button"
        className="tv-btn tv-btn-ghost"
        disabled={disabled}
        onClick={() => onChange?.(appendChecklistDraftItem(list))}
      >
        <PlusIcon /> Добавить пункт чек-листа
      </button>
    </div>
  );
});

/*
 * Форма задачи с постепенным раскрытием: на виду только тема, описание и исполнитель,
 * остальное добавляется кнопками-чипами. Одна и та же форма обслуживает создание и правку —
 * при правке секции с уже заполненными значениями раскрыты сразу.
 */
const COMPOSER_SECTIONS = [
  {
    id: 'priority',
    label: 'Срочность',
    hasValue: (v) => (v.priority || 'normal') !== 'normal',
    summary: (v) => PRIORITY_META[v.priority]?.label,
    clear: () => ({ priority: 'normal' }),
  },
  {
    id: 'tag',
    label: 'Тип',
    hasValue: (v) => (v.tag || 'task') !== 'task',
    summary: (v) => TAG_META[v.tag]?.label,
    clear: () => ({ tag: 'task' }),
  },
  {
    id: 'deadline',
    label: 'Дедлайн',
    hasValue: (v) => Boolean(v.dueAt),
    /* Подписи на чипе нет: открытый «Дедлайн» показывает день и время прямо под
       чипами, а закрыть его — значит снять срок. Подпись была бы дублем поля, а
       на телефоне её длина («Пт, 27 нояб., 18:00») переносила чипы на новую
       строку, и строка срока уезжала из-под пальца при выборе быстрого срока. */
    clear: () => ({ dueAt: '', reminderMinutes: '' }),
    // Появился срок — сразу предлагаем напомнить за день, как просили по умолчанию.
    open: (v) => ({ reminderMinutes: v.reminderMinutes || String(REMINDER_DEFAULT_MINUTES) }),
  },
  {
    id: 'estimate',
    label: 'Оценка',
    hasValue: (v) => Number(v.estimateMinutes) > 0,
    summary: (v) => formatSpentMinutes(Number(v.estimateMinutes)),
    clear: () => ({ estimateMinutes: '' }),
  },
  {
    id: 'origin',
    label: 'Кто поручил',
    hasValue: (v) => Boolean(v.requestedById || String(v.requestedByName || '').trim()),
    summary: (v, files, ctx) => (
      v.requestedById
        ? (ctx?.recipients || []).find((person) => String(person.id) === String(v.requestedById))?.name
        : String(v.requestedByName || '').trim()
    ),
    clear: () => ({ requestedById: '', requestedByName: '' }),
  },
  {
    id: 'checklist',
    label: 'Чек-лист',
    hasValue: (v) => formChecklistItems(v.checklistItems).length > 0,
    summary: (v) => {
      const count = formChecklistItems(v.checklistItems).length;
      return count ? `${count} ${pluralRu(count, 'пункт', 'пункта', 'пунктов')}` : '';
    },
    clear: () => ({ checklistItems: [createEmptyChecklistDraftItem()] }),
  },
  {
    id: 'recurrence',
    label: 'Повтор',
    // У повтора регламента (итерации) расписания нет — оно у шаблона, и чип
    // показывал бы «Ежедневно» у повтора еженедельного шаблона.
    hiddenForRepeatCopy: true,
    hasValue: (v) => Boolean(v.isRegulation),
    summary: (v) => RECURRENCE_OPTIONS.find((item) => item.value === (v.recurrenceType || 'daily'))?.label,
    clear: () => ({ isRegulation: false, recurrenceType: '' }),
    open: () => ({ isRegulation: true, recurrenceType: 'daily' }),
  },
  {
    // Наблюдатели (задача #401): видят задачу на доске проекта, но не работают с ней.
    id: 'watchers',
    label: 'Наблюдатели',
    requiresWatchers: true,
    hasValue: (v) => (v.watcherIds || []).length > 0,
    summary: (v) => {
      const count = (v.watcherIds || []).length;
      return count ? String(count) : '';
    },
    clear: () => ({ watcherIds: [] }),
  },
  {
    id: 'files',
    label: 'Файлы',
    requiresFiles: true,
    hasValue: (v, files) => filesFromList(files).length > 0,
    summary: (v, files) => {
      const count = filesFromList(files).length;
      return count ? `${count} ${pluralRu(count, 'файл', 'файла', 'файлов')}` : '';
    },
  },
];

const NO_COMPOSER_ISSUE = () => '';

const TaskComposerForm = ({
  values,
  onChange,
  recipients = [],
  isRecipientsLoading = false,
  disabled = false,
  files = null,
  onFilesChange = null,
  showBacklogToggle = false,
  currentUserId = 0,
  // Чего не хватает в брифе продакшна: ключ поля → текст под полем ('' — всё в порядке).
  issueFor = NO_COMPOSER_ISSUE,
  // Проекты-доски (задача #401): куда человек вправе ставить задачи.
  projects = [],
}) => {
  const assigneeIds = Array.isArray(values.assigneeIds) ? values.assigneeIds : [];
  const watcherIds = Array.isArray(values.watcherIds) ? values.watcherIds : [];
  /* Проект — в списке те, куда можно ставить, плюс текущий проект задачи: в
     правке он мог уйти в архив, и пустое поле читалось бы как «проекта нет». */
  const projectOptions = useMemo(() => {
    const list = creatableProjects(projects);
    const current = findProject(projects, values.projectId);
    if (current && !list.some((project) => String(project.id) === String(current.id))) list.push(current);
    return list.map((project) => ({ value: String(project.id), label: projectLabel(project) }));
  }, [projects, values.projectId]);
  const selectedProject = findProject(projects, values.projectId);
  // Наблюдателей зовут из тех, кто видит на доске только свои задачи: исполнители
  // задачи и сам постановщик видят её по своей роли, остальные люди доски — все её задачи.
  const watcherPeople = useMemo(() => {
    const candidates = watcherCandidates(selectedProject, [...assigneeIds, String(currentUserId || '')]);
    const chosen = new Set(watcherIds.map(String));
    // Уже выбранные остаются в списке, даже если их только что сделали исполнителями.
    (Array.isArray(selectedProject?.members) ? selectedProject.members : []).forEach((person) => {
      if (chosen.has(String(person.id)) && !candidates.some((item) => String(item.id) === String(person.id))) {
        candidates.push(person);
      }
    });
    return candidates;
  }, [selectedProject, assigneeIds, watcherIds, currentUserId]);
  const watcherOptions = useMemo(
    () => watcherPeople.map((person) => ({ value: String(person.id), label: person.name })),
    [watcherPeople]
  );
  const supportsWatchers = Boolean(selectedProject) && (watcherOptions.length > 0 || watcherIds.length > 0);
  const projectIssue = issueFor('project');
  const projectSelectId = useId();
  const sectionSelectId = useId();
  // Разделы выбранной доски: «Без раздела» — первым, задача может быть и вне их.
  const sectionChoices = useMemo(() => {
    const list = sectionsOf(selectedProject);
    return list.length
      ? [{ value: '', label: 'Без раздела' }, ...list.map((section) => ({ value: String(section.id), label: section.name }))]
      : [];
  }, [selectedProject]);
  /* Исполнители доски (владелец 26.11.2026: «у каждой доски свои исполнители, их
     можно выбирать, а остальные как участники»): задачу доски поручают только её
     исполнителям — всем разом («Все») или поштучно; участников в списке нет.
     Доска без людей — поле прежнее, доска с людьми без исполнителей — пустое. */
  const boardPeople = useMemo(() => boardAssignees(selectedProject), [selectedProject]);
  const hasBinding = hasBoardPeople(selectedProject);
  const noExecutors = boardWithoutExecutors(selectedProject);
  /* Продакшн (задача #354) виден в «Типе» всегда, а исполнители у него — только
     сотрудники маркетинга (уточнение владельца 16.10.2026): при этом типе в
     выборе исполнителя остаются одни маркетологи. Маркетолога узнаём и среди
     исполнителей доски: в ваших получателях их может не быть. */
  const isProduction = values.tag === PRODUCTION_TAG;
  const marketingIds = useMemo(() => productionPersonIds([...recipients, ...boardPeople]), [recipients, boardPeople]);
  // Пока получатели грузятся (список пуст), при смене доски никого не выкидываем.
  const recipientIds = useMemo(
    () => (recipients.length ? new Set(recipients.map((person) => String(person.id))) : null),
    [recipients]
  );
  const me = String(currentUserId || '');
  const everyoneIds = useMemo(
    () => allBoardAssigneeIds(selectedProject, { currentUserId, allowedIds: isProduction ? marketingIds : null }),
    [selectedProject, currentUserId, isProduction, marketingIds]
  );
  // Выбор «Все / Конкретные люди» — когда исполнителей у доски хотя бы двое: «все» из одного — не выбор.
  const offersAll = hasBinding && everyoneIds.length >= 2;
  const isAll = offersAll && Boolean(values.assignAll);
  const pickProject = (next) => {
    const projectId = String(next ?? '');
    const nextProject = findProject(projects, projectId);
    const sameProject = projectId === String(values.projectId || '');
    // Сменили доску — остаются наблюдатели, у кого есть доступ к новой, а раздел
    // сбрасывается: разделы у каждой доски свои. Исполнители — те, кто исполнитель
    // и новой доски; «Все» — все исполнители уже новой доски. Выбор человека форма
    // больше не подменяет своей подсказкой.
    onChange({
      projectId,
      projectAuto: false,
      sectionId: sameProject ? values.sectionId : '',
      watcherIds: keepWatchersForProject(watcherIds, nextProject),
      ...(sameProject ? {} : assigneesAfterProjectChange(values, nextProject, {
        currentUserId,
        allowedIds: isProduction ? productionPersonIds([...recipients, ...boardAssignees(nextProject)]) : null,
        recipientIds,
      })),
    });
  };
  // Раздел, выбранный человеком, — тоже его решение о месте задачи.
  const pickSection = (next) => onChange({ sectionId: String(next ?? ''), projectAuto: false });
  const supportsFiles = typeof onFilesChange === 'function';
  const isRepeatCopy = Boolean(values.regulationParentId);
  const sections = useMemo(
    () => COMPOSER_SECTIONS.filter((section) => (
      (!section.requiresFiles || supportsFiles) && (!section.requiresWatchers || supportsWatchers)
      && !(section.hiddenForRepeatCopy && isRepeatCopy)
    )),
    [supportsFiles, supportsWatchers, isRepeatCopy]
  );
  const sectionCtx = useMemo(() => ({ recipients }), [recipients]);
  const assigneeSelectId = useId();
  /* Кому можно поручить: у доски с людьми — только её исполнители, у доски без
     людей — все получатели. Уже выбранные остаются в списке, даже если они не
     исполнители доски (задачу поставили раньше или человека с тех пор сделали
     участником): их можно снять, а правка не выкинет их молча. */
  const assignablePeople = useMemo(() => {
    if (!hasBinding) return recipients;
    const byId = new Map(boardPeople.map((person) => [String(person.id), person]));
    const members = Array.isArray(selectedProject?.members) ? selectedProject.members : [];
    assigneeIds.forEach((id) => {
      const key = String(id);
      if (byId.has(key)) return;
      // Ставший участником исполнитель из другого отдела есть только в составе
      // доски — без него поле показало бы «Выбрано: 1» вместо имени.
      const known = recipients.find((person) => String(person.id) === key)
        || members.find((person) => String(person.id) === key);
      if (known) byId.set(key, known);
    });
    return [...byId.values()];
  }, [hasBinding, boardPeople, selectedProject, recipients, assigneeIds]);
  // Имена и лица в кнопке поля — и исполнителей доски, и получателей.
  const fieldPeople = useMemo(
    () => (hasBinding ? [...recipients, ...assignablePeople] : recipients),
    [hasBinding, recipients, assignablePeople]
  );
  /* В получателях — все админы и супервайзеры компании: в нативном списке
     нужного человека приходилось искать глазами, поэтому здесь селектор с поиском. */
  const recipientOptions = useMemo(
    () => assignablePeople.map((person) => ({
      value: String(person.id),
      label: person.role
        ? `${person.name} (${recipientRoleLabel(person)})`
        : person.name,
    })),
    [assignablePeople]
  );
  // «Кто поручил» — тот же список без роли: строка вспомогательная, роль в ней лишняя.
  const requesterOptions = useMemo(
    () => recipients.map((person) => ({ value: String(person.id), label: person.name })),
    [recipients]
  );
  /* У продакшна в списке — маркетинг и те, кто уже выбран (их можно снять: у
     старой задачи маркетолог мог с тех пор сменить отдел). */
  const assigneeOptions = useMemo(
    () => (isProduction
      ? recipientOptions.filter((option) => marketingIds.has(option.value)
        || assigneeIds.some((id) => String(id) === option.value))
      : recipientOptions),
    [isProduction, recipientOptions, marketingIds, assigneeIds]
  );
  // «Себе» — если вам можно поручить: у доски с людьми — только если вы её исполнитель.
  const canAssignSelf = Number(currentUserId) > 0
    && assignablePeople.some((person) => Number(person.id) === Number(currentUserId))
    && (!isProduction || marketingIds.has(String(currentUserId)));
  const isSelfAssigned = assigneeIds.some((id) => String(id) === me);

  /* «Все» держит состав в согласии с доской: сменили тип или обновился состав
     доски — исполнители следуют за ними, «Себе» остаётся. Доска перестала
     предлагать «Все» (исполнителей меньше двух) — поле возвращается к выбору
     людей. Пока доску не нашли (проекты ещё грузятся), ничего не трогаем. */
  useEffect(() => {
    if (!values.assignAll || !selectedProject) return;
    if (!offersAll) {
      onChange({
        assignAll: false,
        assigneeIds: assigneeIds.filter((id) => !isProduction || marketingIds.has(String(id))),
      });
      return;
    }
    const keepMe = canAssignSelf && isSelfAssigned;
    const wanted = keepMe ? [...everyoneIds, me] : everyoneIds;
    if (!sameIdSet(wanted, assigneeIds)) onChange({ assigneeIds: wanted });
  }, [values.assignAll, selectedProject, offersAll, everyoneIds, canAssignSelf, isSelfAssigned,
    assigneeIds, isProduction, marketingIds, me, onChange]);

  /* Кого выбирали поштучно до «Все»: вернулись к «Конкретным людям» — они на
     месте, а не пустое поле. */
  const manualPicksRef = useRef([]);
  const pickScope = (scope) => {
    if (disabled) return;
    if (scope === 'all') {
      if (isAll) return;
      manualPicksRef.current = assigneeIds.map(String).filter((id) => id !== me);
      onChange({ assignAll: true, assigneeIds: canAssignSelf && isSelfAssigned ? [...everyoneIds, me] : everyoneIds });
      return;
    }
    if (!isAll) return;
    const restored = keepAssigneesForProject(manualPicksRef.current, selectedProject)
      .filter((id) => id !== me && (!isProduction || marketingIds.has(id)));
    onChange({ assignAll: false, assigneeIds: isSelfAssigned ? [...restored, me] : restored });
  };
  // Кому уйдёт задача при «Все» — по порядку состава, для строки с лицами.
  const allPeople = useMemo(() => {
    if (!isAll) return [];
    const byId = new Map(fieldPeople.map((person) => [String(person.id), person]));
    return assigneeIds.map((id) => byId.get(String(id))).filter(Boolean);
  }, [isAll, fieldPeople, assigneeIds]);
  const iAmExecutor = boardPeople.some((person) => String(person.id) === me);
  /* Под «i» у выбора — зачем он и по строке на вариант (правило владельца для
     выбора сегментами). Без выбора строка поля прежняя, без «i». */
  const assigneeHint = [
    'Поручить задачу этой доски можно только её исполнителям — участников доски в списке нет. Исполнителей и участников назначают админы раздела в окне «Проекты».',
    `«Все» — одна задача на всех исполнителей: взять и сдать её может любой из них.${iAmExecutor ? ' Себя «Все» не включает — для этого кнопка «Себе».' : ''}`,
    `«Конкретные люди» — только отмеченные, до ${TASK_MAX_ASSIGNEES} человек.`,
  ].join(' ');

  /* Выбрали «Продакшн» — из исполнителей уходят все, кто не из маркетинга:
     поставить им этот тип нельзя, и оставлять их в поле значило бы отказ при
     сохранении. */
  const pickTag = (tag) => onChange(tag === PRODUCTION_TAG
    ? { tag, assigneeIds: assigneeIds.filter((id) => marketingIds.has(String(id))) }
    : { tag });

  // Раскрываем то, что уже заполнено: при правке задачи форма сразу показывает суть.
  const [openIds, setOpenIds] = useState(() => new Set(
    sections.filter((section) => section.hasValue(values, files)).map((section) => section.id)
  ));

  const toggleSection = (section) => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(section.id)) {
        next.delete(section.id);
        if (section.clear) onChange(section.clear());
        if (section.requiresFiles) onFilesChange?.([]);
      } else {
        next.add(section.id);
        if (section.open) onChange(section.open(values));
      }
      return next;
    });
  };

  const isOpen = (id) => openIds.has(id);
  const tagIssue = issueFor('tag');
  const deadlineIssue = issueFor('deadline');
  const repeatFrom = repeatAnchorOf(values);

  return (
    <div className="tv-composer">
      <input
        className="tv-composer-title"
        value={values.subject}
        maxLength={255}
        disabled={disabled}
        placeholder="Что нужно сделать?"
        autoFocus
        onChange={(event) => onChange({ subject: event.target.value })}
      />
      {/* У продакшна описание — поле брифа «Описание задания» (то же значение),
          второй раз то же поле наверху было бы дублем. */}
      {!isProduction && (
        <textarea
          className="tv-composer-description"
          value={values.description}
          disabled={disabled}
          placeholder="Описание, контекст, ссылки — необязательно"
          onChange={(event) => onChange({ description: event.target.value })}
        />
      )}

      {/* Проект — доска, на которую попадёт задача (задача #401). Строка есть,
          только когда выбирать есть из чего: без доступа к доскам задача
          ставится как раньше, без проекта. */}
      {projectOptions.length > 0 && (
        <div
          className={`tv-composer-assignee tv-composer-project${projectIssue ? ' is-invalid' : ''}`}
          data-issue-anchor="project"
        >
          <label htmlFor={projectSelectId}>Проект</label>
          <div className="tv-composer-project-body">
            <CustomSelect
              id={projectSelectId}
              className="tv-person-select"
              variant="ios"
              ariaLabel="Проект задачи"
              value={values.projectId || ''}
              options={projectOptions}
              onChange={pickProject}
              disabled={disabled}
              placeholder="Выберите проект"
              searchable={projectOptions.length > 6}
              searchPlaceholder="Найти проект…"
            />
            {projectIssue && <p className="tv-brief-issue">{projectIssue}</p>}
          </div>
        </div>
      )}

      {/* Раздел доски — только у досок, где разделы заведены. */}
      {sectionChoices.length > 0 && (
        <div className="tv-composer-assignee tv-composer-project tv-composer-section">
          <label htmlFor={sectionSelectId}>Раздел</label>
          <div className="tv-composer-project-body">
            <CustomSelect
              id={sectionSelectId}
              className="tv-person-select"
              variant="ios"
              ariaLabel="Раздел доски"
              value={values.sectionId || ''}
              options={sectionChoices}
              onChange={pickSection}
              disabled={disabled}
              searchable={sectionChoices.length > 8}
              searchPlaceholder="Найти раздел…"
            />
          </div>
        </div>
      )}

      {/* Исполнители. У доски с людьми — только её исполнители (26.11.2026): всем
          разом («Все») или поштучно. Без привязки строка прежняя. */}
      <div className={`tv-composer-assignee${offersAll ? ' has-scope' : ''}`}>
        <label htmlFor={isAll ? undefined : assigneeSelectId}>
          {isAll || assigneeIds.length > 1 ? 'Исполнители' : 'Исполнитель'}
        </label>
        <div className="tv-composer-assignee-body">
          {/* «i» — у выбора, а не у подписи: подпись шире не становится, и поле
              стоит в одной колонке с «Проектом» и «Разделом». */}
          {offersAll && (
            <div className="tv-composer-assignee-scope">
              <div className="tv-composer-segmented" role="group" aria-label="Кому поручить задачу">
                <button
                  type="button"
                  className={isAll ? 'is-active' : ''}
                  aria-pressed={isAll}
                  disabled={disabled}
                  onClick={() => pickScope('all')}
                >
                  Все
                  <span className="tv-composer-segment-count">{everyoneIds.length}</span>
                </button>
                <button
                  type="button"
                  className={isAll ? '' : 'is-active'}
                  aria-pressed={!isAll}
                  disabled={disabled}
                  onClick={() => pickScope('people')}
                >
                  Конкретные люди
                </button>
              </div>
              <InfoHint side="left" text={assigneeHint} />
            </div>
          )}
          <div className="tv-composer-assignee-line">
            {isAll ? (
              <AllAssigneesSummary people={allPeople} />
            ) : (
              <AssigneeField
                id={assigneeSelectId}
                value={assigneeIds}
                options={assigneeOptions}
                people={fieldPeople}
                onChange={(next) => onChange({ assigneeIds: next })}
                disabled={disabled || (noExecutors && !assigneeIds.length)}
                isLoading={isRecipientsLoading}
                placeholder={noExecutors
                  ? 'У доски нет исполнителей'
                  : (isProduction
                    ? 'Сотрудник маркетинга'
                    : (hasBinding ? 'Исполнитель доски' : 'Выберите сотрудника'))}
              />
            )}
            {noExecutors && (
              <InfoHint
                side="left"
                text="Задачу этой доски поручают только её исполнителям, а их у доски пока нет. Исполнителей назначают админы раздела в окне «Проекты»."
              />
            )}
            {canAssignSelf && (
              <button
                type="button"
                className={`tv-composer-self ${isSelfAssigned ? 'is-active' : ''}`}
                disabled={disabled || (!isSelfAssigned && !isAll && assigneeIds.length >= TASK_MAX_ASSIGNEES)}
                title={isSelfAssigned
                  ? 'Убрать себя из исполнителей'
                  : (!isAll && assigneeIds.length >= TASK_MAX_ASSIGNEES
                      ? `Больше ${TASK_MAX_ASSIGNEES} исполнителей у задачи не бывает`
                      : 'Добавить себя в исполнители')}
                onClick={() => {
                  if (isSelfAssigned) {
                    onChange({ assigneeIds: assigneeIds.filter((id) => String(id) !== String(currentUserId)) });
                    return;
                  }
                  // Потолок тот же, что у списка: иначе кнопка добавляла бы
                  // одиннадцатого, и сохранение падало бы ошибкой сервера.
                  // У «Все» потолка нет — задача на всех исполнителей доски.
                  if (!isAll && assigneeIds.length >= TASK_MAX_ASSIGNEES) return;
                  onChange({ assigneeIds: [...assigneeIds, String(currentUserId)] });
                }}
              >
                Себе
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="tv-composer-chips">
        {sections.map((section) => {
          const active = isOpen(section.id);
          const summary = active || section.hasValue(values, files)
            ? section.summary?.(values, files, sectionCtx)
            : '';
          return (
            <button
              key={section.id}
              type="button"
              className={`tv-composer-chip ${active ? 'is-active' : ''}`}
              disabled={disabled}
              onClick={() => toggleSection(section)}
            >
              <span className="tv-composer-chip-sign">{active ? '×' : '+'}</span>
              {section.label}
              {summary && <span className="tv-composer-chip-value">{summary}</span>}
            </button>
          );
        })}
        {showBacklogToggle && (
          <button
            type="button"
            className={`tv-composer-chip ${values.isBacklog ? 'is-active' : ''}`}
            disabled={disabled}
            title="Задача попадёт в бэклог, исполнитель не получит уведомление"
            onClick={() => onChange({ isBacklog: !values.isBacklog })}
          >
            <span className="tv-composer-chip-sign">{values.isBacklog ? '×' : '+'}</span>
            В бэклог
          </button>
        )}
      </div>

      {openIds.size > 0 && (
        <div className="tv-composer-details">
          {isOpen('priority') && (
            <div className="tv-composer-row">
              <span className="tv-composer-row-label">Срочность</span>
              <div className="tv-composer-segmented">
                {PRIORITY_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={values.priority === option.value ? 'is-active' : ''}
                    disabled={disabled}
                    onClick={() => onChange({ priority: option.value })}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {isOpen('tag') && (
            <div
              className={`tv-composer-row tv-composer-type-row${tagIssue ? ' is-invalid' : ''}`}
              data-issue-anchor="tag"
            >
              <span className="tv-composer-row-label">Тип</span>
              <div className="tv-composer-row-body">
                <div className="tv-composer-segmented" role="group" aria-label="Тип задачи">
                  {TAG_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={values.tag === option.value ? 'is-active' : ''}
                      aria-pressed={values.tag === option.value}
                      disabled={disabled}
                      onClick={() => pickTag(option.value)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                {tagIssue && <p className="tv-brief-issue">{tagIssue}</p>}
              </div>
            </div>
          )}

          {/* Бриф — сразу под выбранным типом: белой карточкой на сером поле
              панели, как сгруппированные строки iOS. */}
          {isOpen('tag') && isProduction && (
            <ProductionBriefFields
              brief={values.productionBrief || emptyProductionBrief()}
              onChange={(next) => onChange({ productionBrief: next })}
              description={values.description}
              onDescriptionChange={(next) => onChange({ description: next })}
              disabled={disabled}
              issueFor={issueFor}
            />
          )}

          {/* Дедлайн — день в календаре и время, а не «через N дней, часов и
              минут»: до нужного дня человеку приходилось считать самому. */}
          {isOpen('deadline') && (
            <div
              className={`tv-composer-row tv-composer-deadline${deadlineIssue ? ' is-invalid' : ''}`}
              data-issue-anchor="deadline"
            >
              <span className="tv-composer-row-label">
                Дедлайн
                {repeatFrom && (
                  <InfoHint
                    text={`Задача повторяется: здесь срок ближайшего повтора — он появится ${deadlineSummary(repeatFrom).toLowerCase()}. Следующие повторы получат такой же срок от своего начала.`}
                  />
                )}
              </span>
              <div className="tv-composer-row-body">
                <TaskDeadlineField
                  value={values.dueAt}
                  disabled={disabled}
                  presets={!repeatFrom}
                  fromDate={splitLocalDateTime(repeatFrom).date}
                  onChange={(next) => onChange({ dueAt: next || '' })}
                />
                {deadlineIssue && <p className="tv-brief-issue">{deadlineIssue}</p>}
                <div title={values.dueAt ? '' : 'Сначала задайте дедлайн'}>
                  <CustomSelect
                    className="tv-person-select"
                    variant="ios"
                    ariaLabel="Напомнить в Telegram"
                    value={String(normalizeReminderMinutes(values.reminderMinutes) || 0)}
                    options={COMPOSER_REMINDER_OPTIONS}
                    disabled={disabled || !values.dueAt}
                    onChange={(next) => onChange({ reminderMinutes: String(next) })}
                  />
                </div>
              </div>
            </div>
          )}

          {isOpen('estimate') && (
            <div className="tv-composer-row">
              <span className="tv-composer-row-label">Оценка</span>
              <div className="tv-composer-row-body">
                <div className="tv-composer-presets">
                  {[30, 60, 120, 240, 480].map((minutes) => (
                    <button
                      key={minutes}
                      type="button"
                      className={Number(values.estimateMinutes) === minutes ? 'is-active' : ''}
                      disabled={disabled}
                      onClick={() => onChange({ estimateMinutes: String(minutes) })}
                    >
                      {formatSpentMinutes(minutes)}
                    </button>
                  ))}
                </div>
                <input className="tv-input" type="number" min="0" step="15" placeholder="минут"
                  value={values.estimateMinutes} disabled={disabled}
                  onChange={(event) => onChange({ estimateMinutes: event.target.value })} />
              </div>
            </div>
          )}

          {isOpen('origin') && (
            <div className="tv-composer-row">
              <span className="tv-composer-row-label">Кто поручил</span>
              <div className="tv-composer-row-body">
                <div className="tv-composer-segmented">
                  <button
                    type="button"
                    className={values.requestedById ? 'is-active' : ''}
                    disabled={disabled}
                    onClick={() => onChange({ requestedByName: '' })}
                  >
                    Сотрудник
                  </button>
                  <button
                    type="button"
                    className={!values.requestedById ? 'is-active' : ''}
                    disabled={disabled}
                    onClick={() => onChange({ requestedById: '' })}
                  >
                    Другой источник
                  </button>
                </div>
                <CustomSelect
                  className="tv-person-select"
                  variant="ios"
                  ariaLabel="Кто поручил"
                  value={values.requestedById}
                  options={requesterOptions}
                  onChange={(next) => onChange({ requestedById: String(next ?? ''), requestedByName: '' })}
                  disabled={disabled || isRecipientsLoading}
                  placeholder={isRecipientsLoading ? 'Загрузка...' : 'Выбрать сотрудника…'}
                  searchable
                  searchPlaceholder="Поиск по имени…"
                />
                {!values.requestedById && (
                  <input
                    className="tv-input"
                    placeholder="Например: директор на планёрке, клиент Wolt"
                    maxLength={160}
                    value={values.requestedByName}
                    disabled={disabled}
                    onChange={(event) => onChange({ requestedByName: event.target.value, requestedById: '' })}
                  />
                )}
                <p className="tv-field-hint">Не заполнено — задача считается своей инициативой.</p>
              </div>
            </div>
          )}

          {isOpen('recurrence') && (
            <div className="tv-composer-row">
              <span className="tv-composer-row-label">Повтор</span>
              <div className="tv-composer-row-body tv-composer-row-inline">
                <div className="tv-composer-segmented" role="group" aria-label="Как часто повторять">
                  {RECURRENCE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={(values.recurrenceType || 'daily') === option.value}
                      className={(values.recurrenceType || 'daily') === option.value ? 'is-active' : ''}
                      disabled={disabled}
                      onClick={() => onChange({ isRegulation: true, recurrenceType: option.value })}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <input className="tv-input" type="number" min="1" max="365" title="Каждые N периодов"
                  value={values.recurrenceInterval} disabled={disabled}
                  onChange={(event) => onChange({ recurrenceInterval: event.target.value })} />
              </div>
            </div>
          )}

          {isOpen('checklist') && (
            <div className="tv-composer-row is-stacked">
              <span className="tv-composer-row-label">Чек-лист</span>
              <ChecklistDraftEditor
                items={values.checklistItems}
                disabled={disabled}
                onChange={(next) => onChange({ checklistItems: next })}
              />
            </div>
          )}

          {supportsWatchers && isOpen('watchers') && (
            <div className="tv-composer-row">
              <span className="tv-composer-row-label">
                Наблюдатели
                <InfoHint
                  side="left"
                  text="Видят задачу на доске, но не работают с ней. Позвать можно тех, кто видит на доске только свои задачи: остальные люди доски и так видят все её задачи."
                />
              </span>
              <div className="tv-composer-row-body">
                <AssigneeField
                  value={watcherIds}
                  options={watcherOptions}
                  people={watcherPeople}
                  onChange={(next) => onChange({ watcherIds: next })}
                  disabled={disabled}
                  ariaLabel="Наблюдатели"
                  placeholder="Кому показать задачу"
                  maxSelected={TASK_MAX_WATCHERS}
                />
              </div>
            </div>
          )}

          {supportsFiles && isOpen('files') && (
            <div className="tv-composer-row is-stacked">
              <TaskFileDropzone
                files={files}
                disabled={disabled}
                onChange={onFilesChange}
                title="Вложения"
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

/* Уточнения по задаче: дополнение постановки, запрос информации и ответ на него.
   Отдельно от отчётов о работе — это переписка по заданию, а не факт работы. */
const TASK_MESSAGE_KIND_LABEL = {
  note: 'Дополнение',
  request: 'Не хватает информации',
  answer: 'Ответ на запрос',
};

const TASK_MESSAGE_PLACEHOLDER = {
  note: 'Что добавить к задаче',
  request: 'Чего не хватает, чтобы продолжить',
  answer: 'Ответ исполнителю',
};

const TASK_MESSAGE_SUBMIT_LABEL = {
  note: 'Отправить дополнение',
  request: 'Отправить запрос',
  answer: 'Отправить ответ',
};

const TaskRow = React.memo(({ task, onClick, onPin, isPinned, compact = false }) => {
  const sm = STATUS_META[task.status] || { label: task.status, badge: 'tv-badge-gray', dot: '#ccc' };
  const tm = TAG_META[task.tag]       || { label: task.tag || '—', badge: 'tv-badge-gray' };
  const pm = PRIORITY_META[task.priority] || PRIORITY_META.normal;
  const deadlineLabel = taskDeadlineLabel(task);
  const creatorName = task?.creator?.name || '—';
  const assigneePeople = taskAssignees(task);
  const priorityCls = task.priority === 'critical' ? 'is-critical' : task.priority === 'urgent' ? 'is-urgent' : '';
  // Тон статуса — из той же метки, что и везде: tv-badge-amber → is-amber.
  const statusTone = String(sm.badge || '').replace(/^tv-badge-/, '') || 'gray';
  const isDone = DONE_STATUSES.has(task.status);

  /* Телефон: тема в две строки и одна строка сути под ней. Бейджи в ряд
     съедали ширину, и от темы оставалось «Обновить и…». Цвет — только там, где
     он что-то значит: точка статуса, срочность, просрочка, вопрос без ответа.
     «Задача» — тип по умолчанию и не пишется; кто кому поручил, видно по лицам. */
  const taskKey = taskKeyOf(task);
  if (compact) {
    const facts = [
      <span key="status" className="tv-task-row-status"><i style={{ background: sm.dot }} />{sm.label}</span>,
    ];
    // Ключ задачи проекта — первым: по нему задачу называют в переписке.
    if (taskKey) facts.unshift(<span key="key" className="tv-task-key">{taskKey}</span>);
    if (task.priority === 'urgent' || task.priority === 'critical') {
      facts.push(<span key="priority" className={`tv-task-row-priority is-${task.priority}`}>{pm.label}</span>);
    }
    if (task.tag && task.tag !== 'task') facts.push(<span key="tag">{tm.label}</span>);
    if (task?.due_at) {
      const overdue = isTaskOverdue(task);
      facts.push(
        <span key="due" className={overdue ? 'is-overdue' : ''}>
          {overdue ? 'Просрочено' : 'До'} {fmtShortDateTime(task.due_at)}
        </span>
      );
    }
    if (task?.info_request) facts.push(<span key="info" className="tv-clar-waiting">Ждёт ответа</span>);
    return (
      <div className="tv-task-row is-compact" onClick={() => onClick(task)}>
        <span className="tv-task-row-main">
          <span className="tv-task-row-subject">{task.subject || 'Без темы'}</span>
          <span className="tv-task-row-facts">
            {facts.map((fact, index) => (
              <React.Fragment key={fact.key}>
                {index > 0 && <span className="tv-task-row-sep" aria-hidden="true">·</span>}
                {fact}
              </React.Fragment>
            ))}
          </span>
        </span>
        <AssigneeStack people={assigneePeople} />
        <ChevronRight />
      </div>
    );
  }
  /* Строка списка iOS: кружок статуса слева, тема, под ней — кто кому и сроки;
     справа — срочность и тип (только не по умолчанию), статус словом, дата.
     «Задача» — тип по умолчанию и не пишется, как в строке телефона. */
  return (
    <div className={`tv-task-row ${priorityCls}`} onClick={() => onClick(task)}>
      <span className={`tv-task-row-check is-${statusTone}`} aria-hidden="true">
        {isDone && <Check size={12} strokeWidth={3.2} />}
      </span>
      <span className="tv-task-row-main">
        <span className="tv-task-row-subject">{task.subject || 'Без темы'}</span>
        <span className="tv-task-row-flow">
          {taskKey && <span className="tv-task-key">{taskKey}</span>}
          <span className="tv-task-row-flow-name" title={creatorName}>{creatorName}</span>
          <span className="tv-task-row-flow-arrow">→</span>
          <AssigneeStack people={assigneePeople} />
          {deadlineLabel && <span className={`tv-deadline-chip ${isTaskOverdue(task) ? 'is-overdue' : ''}`}>{deadlineLabel}</span>}
          {/* Единственное «лишнее» состояние, которое стоит показать всем: по задаче
              задали вопрос, и до ответа она не двигается. */}
          {task?.info_request && <span className="tv-clar-waiting">Ждёт ответа</span>}
        </span>
      </span>
      <span className="tv-task-row-meta">
        {task.tag && task.tag !== 'task' && <span className={`tv-badge ${tm.badge}`}>{tm.label}</span>}
        {task?.priority && task.priority !== 'normal' && <span className={`tv-badge ${pm.badge}`}>{pm.label}</span>}
        <span className={`tv-task-row-state is-${statusTone}`}>{sm.label}</span>
        <span className="tv-task-row-date">{fmt(task.created_at)}</span>
        {typeof onPin === 'function' && (
          <button
            type="button"
            className={`tv-row-pin-btn ${isPinned ? 'is-pinned' : ''}`}
            title={isPinned ? 'Открепить задачу' : 'Закрепить задачу'}
            aria-label={isPinned ? 'Открепить задачу' : 'Закрепить задачу'}
            onClick={(event) => {
              event.stopPropagation();
              onPin(task);
            }}
          >
            {isPinned ? <PinOff size={14} strokeWidth={2} /> : <Pin size={14} strokeWidth={2} />}
          </button>
        )}
        <ChevronRight />
      </span>
    </div>
  );
});

/* ─── TaskDrawer — defined outside to avoid remount ─── */
/* Журнал отчётов о проделанной работе: промежуточные записи + итоговый отчёт при сдаче. */
const TaskReportsBlock = ({
  task,
  reports,
  canWrite,
  currentUserId,
  actionLoadingKey,
  onSubmitReport,
  onPatchReport,
  onRemoveReport,
}) => {
  const [composerOpen, setComposerOpen] = useState(false);
  const [draftBody, setDraftBody] = useState('');
  const [draftSpent, setDraftSpent] = useState('');
  const [editingId, setEditingId] = useState(null);
  const [editBody, setEditBody] = useState('');
  const [editSpent, setEditSpent] = useState('');

  const spentTotal = useMemo(
    () => reports.reduce((sum, item) => sum + (Number(item?.spent_minutes) || 0), 0),
    [reports]
  );
  const estimate = Number(task?.estimate_minutes || 0);
  const isBusy = !!actionLoadingKey;

  const closeComposer = () => {
    setComposerOpen(false);
    setDraftBody('');
    setDraftSpent('');
  };

  const submitDraft = async () => {
    const ok = await onSubmitReport(task, {
      body: draftBody,
      spentMinutes: parseSpentInput(draftSpent),
      kind: 'progress',
    });
    if (ok) closeComposer();
  };

  const startEdit = (report) => {
    setEditingId(report.id);
    setEditBody(report.body || '');
    setEditSpent(report.spent_minutes ? String(report.spent_minutes) : '');
  };

  const submitEdit = async (report) => {
    const ok = await onPatchReport(task, report, {
      body: editBody,
      spentMinutes: parseSpentInput(editSpent),
    });
    if (ok) setEditingId(null);
  };

  return (
    <div>
      <div className="tv-reports-header">
        <p className="tv-block-label" style={{ marginBottom: 0 }}>Отчёты о работе</p>
        <span className="tv-reports-meta">
          {spentTotal > 0 && <span>Затрачено {formatSpentMinutes(spentTotal)}</span>}
          {spentTotal > 0 && estimate > 0 && (
            <span className={spentTotal > estimate ? 'is-over' : ''}>
              из {formatSpentMinutes(estimate)} по оценке
            </span>
          )}
          {spentTotal === 0 && estimate > 0 && <span>Оценка {formatSpentMinutes(estimate)}</span>}
        </span>
      </div>

      {reports.length === 0 && (
        <p className="tv-reports-empty">
          Отчётов пока нет. Опишите, что сделано — это остаётся в истории задачи.
        </p>
      )}

      {reports.length > 0 && (
        <div className="tv-reports-list">
          {reports.map((report) => {
            const isAuthor = Number(report?.author_id || 0) === currentUserId;
            const isEditing = editingId === report.id;
            const rowBusy = actionLoadingKey === `${task.id}:report:${report.id}`;
            return (
              <div key={report.id} className={`tv-report-card ${report.kind === 'completion' ? 'is-final' : ''}`}>
                <div className="tv-report-top">
                  <span className="tv-report-kind">{REPORT_KIND_LABEL[report.kind] || 'Отчёт'}</span>
                  <span className="tv-report-author">{report.author_name || '—'}</span>
                  <span className="tv-report-time">{fmt(report.created_at)}</span>
                  {Number(report.spent_minutes) > 0 && (
                    <span className="tv-report-spent">{formatSpentMinutes(report.spent_minutes)}</span>
                  )}
                  {isAuthor && !isEditing && (
                    <span className="tv-report-actions">
                      <button type="button" disabled={isBusy} onClick={() => startEdit(report)}>Правка</button>
                      <button type="button" disabled={isBusy} onClick={() => onRemoveReport(task, report)}>Удалить</button>
                    </span>
                  )}
                </div>
                {isEditing ? (
                  <div className="tv-report-form">
                    <textarea
                      className="tv-textarea"
                      value={editBody}
                      disabled={rowBusy}
                      onChange={(event) => setEditBody(event.target.value)}
                    />
                    <div className="tv-report-form-row">
                      <input
                        className="tv-input"
                        placeholder="Затрачено: 3ч, 90, 1д 2ч"
                        value={editSpent}
                        disabled={rowBusy}
                        onChange={(event) => setEditSpent(event.target.value)}
                      />
                      <button type="button" className="tv-btn tv-btn-ghost" disabled={rowBusy}
                        onClick={() => setEditingId(null)}>Отмена</button>
                      <button type="button" className="tv-btn tv-btn-primary" disabled={rowBusy}
                        onClick={() => submitEdit(report)}>Сохранить</button>
                    </div>
                  </div>
                ) : (
                  <p className="tv-report-body">{report.body}</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {canWrite && !composerOpen && (
        <button
          type="button"
          className="tv-btn tv-btn-ghost tv-reports-add"
          disabled={isBusy}
          onClick={() => setComposerOpen(true)}
        >
          + Добавить отчёт
        </button>
      )}

      {canWrite && composerOpen && (
        <div className="tv-report-form tv-report-composer">
          <textarea
            className="tv-textarea"
            placeholder="Что сделано по задаче"
            autoFocus
            value={draftBody}
            disabled={isBusy}
            onChange={(event) => setDraftBody(event.target.value)}
          />
          <div className="tv-report-form-row">
            <input
              className="tv-input"
              placeholder="Затрачено: 3ч, 90, 1д 2ч"
              value={draftSpent}
              disabled={isBusy}
              onChange={(event) => setDraftSpent(event.target.value)}
            />
            <button type="button" className="tv-btn tv-btn-ghost" disabled={isBusy} onClick={closeComposer}>
              Отмена
            </button>
            <button type="button" className="tv-btn tv-btn-primary" disabled={isBusy} onClick={submitDraft}>
              {actionLoadingKey === `${task.id}:report` ? 'Сохраняю...' : 'Сохранить отчёт'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

/* Лента уточнений: постановщик дополняет постановку в любой момент, исполнитель
   просит то, чего не хватает. Заголовок и разделитель рисует сам блок — когда
   писать нечего и лента пуста, раздела в карточке нет вовсе. */
const TaskClarificationsBlock = ({
  task,
  messages,
  currentUserId,
  currentUserRole,
  actionLoadingKey,
  onSubmitMessage,
  onWithdrawRequest,
  downloadAttachment,
  photoState,
  onOpenPhoto,
  onOpenFile,
}) => {
  const [composerKind, setComposerKind] = useState('');
  const [draftBody, setDraftBody] = useState('');
  const [draftFiles, setDraftFiles] = useState([]);

  const assigneeIdSet = useMemo(
    () => new Set(taskAssignees(task).map((person) => Number(person.id))),
    [task]
  );
  const creatorId = Number(task?.creator?.id || 0);
  const requestedById = Number(task?.requested_by?.id || 0);
  const isAssignee = assigneeIdSet.has(currentUserId);
  // Те же права, что проверяет API: дополняет и отвечает сторона постановки.
  const isOwnerSide = currentUserId === creatorId
    || (requestedById > 0 && currentUserId === requestedById)
    || isAdminLikeRole(currentUserRole);
  const answerAuthorityId = requestedById || creatorId;
  const openRequest = task?.info_request || null;
  const canAddNote = typeof onSubmitMessage === 'function' && isOwnerSide;
  const canAnswer = canAddNote && !!openRequest;
  // Статусы те же, по которым запрос попадает в «ждут вас»: на сданной задаче
  // уведомление ушло бы, а бейдж молчал.
  const canAsk = typeof onSubmitMessage === 'function'
    && isAssignee
    && ['assigned', 'in_progress', 'returned'].includes(String(task?.status || ''))
    && answerAuthorityId > 0
    && answerAuthorityId !== currentUserId;
  const isMyRequest = !!openRequest && Number(openRequest.author_id || 0) === currentUserId;
  const isBusy = !!actionLoadingKey;

  if (!messages.length && !canAddNote && !canAsk) return null;

  const closeComposer = () => {
    setComposerKind('');
    setDraftBody('');
    setDraftFiles([]);
  };

  const openComposer = (kind) => {
    setComposerKind(kind);
    setDraftBody('');
    setDraftFiles([]);
  };

  const submitDraft = async () => {
    const ok = await onSubmitMessage(task, {
      kind: composerKind,
      body: draftBody,
      files: draftFiles,
    });
    if (ok) closeComposer();
  };

  return (
    <>
      <hr className="tv-divider" />
      <div>
        <div className="tv-clar-head">
          <p className="tv-block-label" style={{ marginBottom: 0 }}>Уточнения</p>
          {openRequest && (
            <span className="tv-clar-pending">
              {isOwnerSide && !isAssignee ? 'Ждут вашего ответа' : 'Ждём ответа постановщика'}
            </span>
          )}
        </div>

        {messages.length === 0 && (
          <p className="tv-clar-empty">
            {canAsk
              ? 'Не хватает данных для работы — попросите их здесь, постановщик получит уведомление.'
              : 'Здесь можно дополнить постановку текстом или файлом в любой момент.'}
          </p>
        )}

        {messages.length > 0 && (
          <div className="tv-clar-list">
            {messages.map((message) => {
              const authorId = Number(message?.author_id || 0);
              // Сторона реплики: исполнитель — любой из состава, поэтому
              // сверяем автора с множеством, а не с одним человеком.
              const side = authorId && assigneeIdSet.has(authorId) ? 'is-assignee' : 'is-owner';
              const isOpenRequest = message.kind === 'request' && !message.resolved_at;
              // Пузырь с фото на телефоне получает ширину заранее (tasks-mobile.css):
              // иначе он сжат по подписи, и плитки прыгают, когда приедет картинка.
              const hasPhotos = Array.isArray(message.attachments) && message.attachments.some(isPreviewablePhoto);
              return (
                <div key={message.id} className={`tv-clar-item ${side}`}>
                  <div className={`tv-clar-bubble ${isOpenRequest ? 'is-open' : ''} ${hasPhotos ? 'has-photos' : ''}`}>
                    <span className="tv-clar-top">
                      <span className="tv-clar-kind">
                        {TASK_MESSAGE_KIND_LABEL[message.kind] || 'Уточнение'}
                      </span>
                      <span>{message.author_name || '—'}</span>
                      <span>{fmt(message.created_at)}</span>
                    </span>
                    {message.body && <p className="tv-clar-body">{message.body}</p>}
                    {Array.isArray(message.attachments) && message.attachments.length > 0 && (
                      <TaskFileGroup
                        attachments={message.attachments}
                        photoState={photoState}
                        onOpenPhoto={onOpenPhoto}
                        onOpenFile={onOpenFile}
                        onDownload={downloadAttachment}
                      />
                    )}
                    {message.kind === 'request' && message.resolved_at && (
                      <span className="tv-clar-resolved">
                        Закрыт · {fmtShortDateTime(message.resolved_at)}
                        {message.resolved_by_name ? ` · ${message.resolved_by_name}` : ''}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {!composerKind && (
          <div className="tv-clar-actions">
            {canAnswer && (
              <button type="button" className="tv-btn tv-btn-primary" disabled={isBusy}
                onClick={() => openComposer('answer')}>
                Ответить на запрос
              </button>
            )}
            {canAddNote && (
              <button type="button" className="tv-btn tv-btn-ghost" disabled={isBusy}
                onClick={() => openComposer('note')}>
                + Дополнить задачу
              </button>
            )}
            {canAsk && !openRequest && (
              <button type="button" className="tv-btn tv-btn-ghost" disabled={isBusy}
                onClick={() => openComposer('request')}>
                Не хватает информации
              </button>
            )}
            {isMyRequest && typeof onWithdrawRequest === 'function' && (
              <button type="button" className="tv-btn tv-btn-ghost" disabled={isBusy}
                onClick={() => onWithdrawRequest(task, openRequest)}>
                Снять запрос
              </button>
            )}
          </div>
        )}

        {composerKind && (
          <div className="tv-clar-form">
            <textarea
              className="tv-textarea"
              placeholder={TASK_MESSAGE_PLACEHOLDER[composerKind] || 'Текст уточнения'}
              autoFocus
              value={draftBody}
              disabled={isBusy}
              onChange={(event) => setDraftBody(event.target.value)}
            />
            <TaskFileDropzone
              files={draftFiles}
              disabled={isBusy}
              onChange={setDraftFiles}
              title="Файлы уточнения"
              prompt="Перетащите файл сюда"
              hint="или нажмите, чтобы выбрать"
            />
            <div className="tv-clar-form-row">
              <button type="button" className="tv-btn tv-btn-ghost" disabled={isBusy} onClick={closeComposer}>
                Отмена
              </button>
              <button type="button" className="tv-btn tv-btn-primary" disabled={isBusy} onClick={submitDraft}>
                {actionLoadingKey === `${task.id}:message`
                  ? 'Отправляю...'
                  : (TASK_MESSAGE_SUBMIT_LABEL[composerKind] || 'Отправить')}
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
};

/* Подсказка «i» у строки «Балл» — по тому, что с этой задачей сделает балл: переезд
   обещаем только там, где он будет (не у начатой и не у шаблона регламента). */
const scoreHintOf = (task) => {
  const base = `Балл ставят суперадмины на совещании: ${PRIORITY_SCORE_HINT}`;
  if (awaitsPriorityScore(task)) return `${base} С первым баллом задача из «Новой» перейдёт в бэклог.`;
  if (unscoreMovesToNew(task, null)) {
    return `${base} Снять балл — нажать на выбранную цифру ещё раз: задача вернётся в «Новую».`;
  }
  return `${base} Снять балл — нажать на выбранную цифру ещё раз.`;
};

const TaskDrawer = React.memo(({
  task, onClose, actionLoadingKey,
  getActionButtons, openCompleteModal, openStatusModal, updateStatus, downloadAttachment, loadAttachmentFile = null,
  onEditTask, onDeleteTask, onTogglePinTask, onCopyTaskLink, onMoveToBacklog, onToggleChecklistItem, onSaveChecklistNote,
  isPinned, currentUserId, currentUserRole, onSubmitReport, onPatchReport, onRemoveReport,
  onSubmitTaskMessage, onWithdrawInfoRequest, isMobileShell = false, loadTaskPhotos, onOpenProduction = null,
  projects = [], onSaveWatchers = null, onSetPriorityScore = null,
}) => {
  const sm = STATUS_META[task.status] || { label: task.status, badge: 'tv-badge-gray' };
  const tm = TAG_META[task.tag]       || { label: task.tag || '—', badge: 'tv-badge-gray' };
  const pm = PRIORITY_META[task.priority] || PRIORITY_META.normal;
  // Файлы, приехавшие с уточнением, показывает само уточнение — в «Файлах
  // задачи» они были бы вторым экземпляром того же файла.
  const attachments     = (Array.isArray(task.attachments) ? task.attachments : [])
    .filter((att) => !att?.message_id);
  const compAttachments = Array.isArray(task.completion_attachments) ? task.completion_attachments : [];
  const messages        = Array.isArray(task.messages)               ? task.messages               : [];
  const history         = Array.isArray(task.history)                ? task.history                : [];
  const checklist       = Array.isArray(task.checklist)              ? task.checklist              : [];
  const progress        = checklistProgress(checklist);
  const deadlineLabel   = taskDeadlineLabel(task);
  const btns            = getActionButtons(task);
  const editBtn         = btns.find((btn) => btn.action === 'edit');
  const deleteBtn       = btns.find((btn) => btn.action === 'delete');
  const footerBtns      = btns.filter((btn) => btn.action !== 'edit' && btn.action !== 'delete');
  const assigneePeople  = taskAssignees(task);
  const assigneeIdSet   = useMemo(
    () => new Set(assigneePeople.map((person) => Number(person.id))),
    [assigneePeople]
  );
  const creatorId       = Number(task?.creator?.id || 0);
  const reports         = Array.isArray(task.reports) ? task.reports : [];
  const priorityScore   = priorityScoreOf(task);
  const canScore        = typeof onSetPriorityScore === 'function' && normalizeRole(currentUserRole) === 'super_admin';
  // Задача, поставленная самому себе без указания источника, — своя инициатива.
  const originLabel     = (creatorId && assigneeIdSet.has(creatorId) && !task?.requested_by)
    ? 'Своя инициатива'
    : 'Постановщик';
  // Отчёт пишут участники задачи — та же проверка, что и на API.
  const canWriteReport  = typeof onSubmitReport === 'function' && (
    assigneeIdSet.has(currentUserId) || creatorId === currentUserId || isAdminLikeRole(currentUserRole)
  );
  // Лист «⋯» с действиями над задачей — только на телефоне.
  const [actionsOpen, setActionsOpen] = useState(false);

  /* Картинки карточки: адреса одним запросом на карточку, просмотр во весь
     экран. Листается внутри того блока, из которого открыли: снимки постановки
     и снимки результата — разные истории, и «вперёд» из постановки не должно
     молча уводить в результат. */
  const photoState = useTaskPhotoPreviews(task, loadTaskPhotos);
  const [photoView, setPhotoView] = useState(null);
  const openPhoto = useCallback((items, index) => {
    if (Array.isArray(items) && index >= 0 && index < items.length) setPhotoView({ items, index });
  }, []);
  const closePhoto = useCallback(() => setPhotoView(null), []);
  /* Остальные файлы — в окне просмотра (FilePreviewSheet), без скачивания.
     Листается, как и снимки, внутри своего блока. Без загрузчика файла
     (карточку открыли не из раздела) кнопки по-прежнему скачивают. */
  const [fileView, setFileView] = useState(null);
  const openFile = useCallback((items, index) => {
    if (Array.isArray(items) && index >= 0 && index < items.length) setFileView({ items, index });
  }, []);
  const closeFile = useCallback(() => setFileView(null), []);
  const stepFile = useCallback((index) => setFileView((prev) => (prev ? { ...prev, index } : prev)), []);
  const canPreviewFiles = typeof loadAttachmentFile === 'function';
  // Другая задача в той же карточке — снимок и файл прежней не должны остаться поверх.
  useEffect(() => { setPhotoView(null); setFileView(null); }, [task?.id]);
  const viewedPhoto = photoView ? photoView.items[photoView.index] : null;
  const viewedPhotoUrl = viewedPhoto ? photoState.previews[Number(viewedPhoto.id)]?.url || '' : '';
  const photoCount = photoView ? photoView.items.length : 0;
  const stepPhoto = (delta) => () => setPhotoView((prev) => (
    prev ? { ...prev, index: Math.min(prev.items.length - 1, Math.max(0, prev.index + delta)) } : prev
  ));

  // ESC key handler
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const resolveHistorySide = useCallback((item) => {
    const changedById = Number(item?.changed_by || 0);
    if (changedById > 0) {
      if (assigneeIdSet.has(changedById)) return 'recipient';
      if (changedById === creatorId) return 'sender';
    }

    const statusCode = String(item?.status_code || '').trim();
    if (statusCode === 'in_progress' || statusCode === 'completed') return 'recipient';
    if (statusCode === 'assigned' || statusCode === 'accepted' || statusCode === 'returned' || statusCode === 'reopened') return 'sender';
    return 'neutral';
  }, [assigneeIdSet, creatorId]);

  /* Тема и метки. На компьютере — в шапке рядом с действиями; на телефоне шапка
     экрана — это «Назад» и «⋯», а тема открывает содержимое крупным заголовком. */
  const taskKey = taskKeyOf(task);
  const watchers = Array.isArray(task?.watchers) ? task.watchers : [];
  /* Наблюдателей постановщик зовёт прямо отсюда: человек не из проекта видит
     задачу, только если у него есть доступ к доске И он наблюдатель (#401). */
  const [watchersOpen, setWatchersOpen] = useState(false);
  const taskProject = findProject(projects, task?.project?.id);
  const watcherPool = useMemo(() => {
    const list = watcherCandidates(taskProject, [...assigneePeople.map((person) => person.id), creatorId]);
    // Уже выбранные — в списке всегда, даже если их уровень с тех пор сменился.
    watchers.forEach((person) => {
      if (!list.some((item) => String(item.id) === String(person.id))) list.push(person);
    });
    return list;
  }, [taskProject, assigneePeople, creatorId, watchers]);
  const canManageWatchers = typeof onSaveWatchers === 'function'
    && Boolean(task?.project?.id) && creatorId === currentUserId && !task?.viewer_is_observer
    && watcherPool.length > 0;
  const titleBlock = (
    <div className="tv-drawer-header-text">
      {/* Ключ задачи проекта (HR-125) над темой — как в трекерах: по нему задачу
          и находят, и называют. Название проекта — в сведениях ниже. */}
      {taskKey && <span className="tv-task-key tv-drawer-key">{taskKey}</span>}
      <h2 className="tv-drawer-title">{task.subject || 'Без темы'}</h2>
      <div className="tv-drawer-badges">
        <span className={`tv-badge ${sm.badge}`}>{sm.label}</span>
        <span className={`tv-badge ${tm.badge}`}>{tm.label}</span>
        {/* «Обычная» — срочность по умолчанию, а не признак: не пишем её нигде —
            ни в строке списка, ни в карточке, ни в закреплённой задаче. */}
        {task?.priority && task.priority !== 'normal' && (
          <span className={`tv-badge ${pm.badge}`}>{pm.label}</span>
        )}
        {task?.is_regulation && <span className="tv-badge tv-badge-teal">Регламент</span>}
      </div>
    </div>
  );

  /* Действия с задачей на телефоне — в листе «⋯», у каждого своё имя: четыре
     значка без подписей в шапке шириной 390 px угадывались бы по картинке, а
     удаление стояло бы вплотную к правке. Лист закрывается в том же кадре, в
     котором открывается окно действия, — запись истории переходит к окну. */
  const runSheetAction = (action) => () => {
    setActionsOpen(false);
    action();
  };
  const sheetActions = [
    typeof onCopyTaskLink === 'function' && {
      key: 'link', label: 'Скопировать ссылку', onClick: runSheetAction(() => onCopyTaskLink(task)),
    },
    /* Закрепить с телефона нельзя: закреплённая задача — настольное плавающее
       окно, и на экране в 390 px оно встаёт карточкой на пол-экрана поверх бара
       разделов. Открепить — можно: закрепление хранится на устройстве, и тем, кто
       закрепил с телефона раньше, иначе от окна не избавиться. */
    typeof onTogglePinTask === 'function' && isPinned && {
      key: 'pin', label: 'Открепить задачу',
      disabled: !!actionLoadingKey, onClick: runSheetAction(() => onTogglePinTask(task)),
    },
    editBtn && {
      key: 'edit', label: editBtn.label, disabled: !!actionLoadingKey, onClick: runSheetAction(() => onEditTask(task)),
    },
    deleteBtn && {
      key: 'delete', label: deleteBtn.label, danger: true,
      disabled: !!actionLoadingKey, onClick: runSheetAction(() => onDeleteTask(task)),
    },
  ].filter(Boolean);

  const header = isMobileShell ? (
    <div className="tv-drawer-header otp-modal-head">
      <button type="button" className="otp-modal-back tv-screen-back" aria-label="Назад" onClick={onClose}>
        <ScreenBackChevron />
      </button>
      {sheetActions.length > 0 && (
        <button
          type="button"
          className="tv-screen-more"
          aria-label="Действия с задачей"
          aria-haspopup="dialog"
          onClick={() => setActionsOpen(true)}
        >
          <MoreHorizontal size={22} strokeWidth={2} />
        </button>
      )}
    </div>
  ) : (
    <div className="tv-drawer-header">
      {titleBlock}
      <div className="tv-drawer-header-actions">
        {typeof onCopyTaskLink === 'function' && (
          <button
            type="button"
            className="tv-icon-btn"
            title="Скопировать ссылку на задачу"
            aria-label="Скопировать ссылку на задачу"
            onClick={() => onCopyTaskLink(task)}
          >
            <Link2 size={15} strokeWidth={2} />
          </button>
        )}
        {typeof onTogglePinTask === 'function' && (
          <button
            type="button"
            className={`tv-icon-btn ${isPinned ? 'is-pinned' : ''}`}
            title={isPinned ? 'Открепить задачу' : 'Закрепить задачу'}
            aria-label={isPinned ? 'Открепить задачу' : 'Закрепить задачу'}
            disabled={!!actionLoadingKey}
            onClick={() => onTogglePinTask(task)}
          >
            {isPinned ? <PinOff size={15} strokeWidth={2} /> : <Pin size={15} strokeWidth={2} />}
          </button>
        )}
        {editBtn && (
          <button
            type="button"
            className="tv-icon-btn"
            title={editBtn.label}
            aria-label={editBtn.label}
            disabled={!!actionLoadingKey}
            onClick={() => onEditTask(task)}
          >
            {actionLoadingKey === `${task.id}:edit`
              ? <FaIcon className="fa-circle-notch fa-spin" />
              : <FaIcon className="fa-pen" />}
          </button>
        )}
        {deleteBtn && (
          <button
            type="button"
            className="tv-icon-btn tv-icon-btn-danger"
            title={deleteBtn.label}
            aria-label={deleteBtn.label}
            disabled={!!actionLoadingKey}
            onClick={() => onDeleteTask(task)}
          >
            {actionLoadingKey === `${task.id}:delete`
              ? <FaIcon className="fa-circle-notch fa-spin" />
              : <FaIcon className="fa-trash-alt" />}
          </button>
        )}
        <button className="tv-close-btn" type="button" onClick={onClose} aria-label="Закрыть (Esc)">
          <CloseIcon />
        </button>
      </div>
    </div>
  );

  const drawerInner = (
      <>
        {header}

        <div className="tv-drawer-body">
          {isMobileShell && titleBlock}

          {/* Participants */}
          <div className="tv-participants">
            {/* Исполнителей столько, сколько отметили. Никого не помечаем
                «основным»: права у них равные, и такая метка была бы неправдой. */}
            {(assigneePeople.length ? assigneePeople : [null]).map((person, index) => (
              <div className="tv-participant" key={person?.id ?? `assignee-${index}`}>
                <AvatarCircle
                  className="tv-participant-avatar"
                  name={person?.name || '—'}
                  avatarUrl={person?.avatar_url || ''}
                />
                <div className="tv-participant-info">
                  <div className="tv-participant-role">Исполнитель</div>
                  <div className="tv-participant-name">{person?.name || '—'}</div>
                </div>
              </div>
            ))}
            <div className="tv-participant">
              <AvatarCircle
                className="tv-participant-avatar"
                name={task?.creator?.name || '—'}
                avatarUrl={task?.creator?.avatar_url || ''}
              />
              <div className="tv-participant-info">
                <div className="tv-participant-role">{originLabel}</div>
                <div className="tv-participant-name">{task?.creator?.name || '—'}</div>
              </div>
            </div>
            {/* Наблюдатели (задача #401) видят задачу на доске проекта, но не работают с ней. */}
            {watchers.map((person) => (
              <div className="tv-participant" key={`watcher-${person.id}`}>
                <AvatarCircle
                  className="tv-participant-avatar"
                  name={person?.name || '—'}
                  avatarUrl={person?.avatar_url || ''}
                />
                <div className="tv-participant-info">
                  <div className="tv-participant-role">Наблюдатель</div>
                  <div className="tv-participant-name">{person?.name || '—'}</div>
                </div>
              </div>
            ))}
            {canManageWatchers && (
              <button
                type="button"
                className="tv-participant tv-participant-add"
                onClick={() => setWatchersOpen(true)}
              >
                <span className="tv-participant-avatar" aria-hidden="true"><PlusIcon /></span>
                <span className="tv-participant-info">
                  <span className="tv-participant-role">Наблюдатели</span>
                  <span className="tv-participant-name">{watchers.length ? 'Изменить' : 'Добавить'}</span>
                </span>
              </button>
            )}
          </div>
          {canManageWatchers && (
            <TaskWatchersSheet
              open={watchersOpen}
              onClose={() => setWatchersOpen(false)}
              taskKey={taskKey}
              projectName={task?.project?.name || ''}
              candidates={watcherPool}
              selectedIds={watchers.map((person) => person.id)}
              onSave={(ids) => onSaveWatchers(task, ids)}
            />
          )}

          <div className="tv-info-grid">
            {task?.project?.name && (
              <div className="tv-info-item"><label>Проект</label><span>{task.project.name}</span></div>
            )}
            {task?.section?.name && (
              <div className="tv-info-item"><label>Раздел</label><span>{task.section.name}</span></div>
            )}
            <div className="tv-info-item"><label>Создано</label><span>{fmt(task.created_at)}</span></div>
            {task?.requested_by?.name && (
              <div className="tv-info-item"><label>Поручил</label><span>{task.requested_by.name}</span></div>
            )}
            <div className="tv-info-item"><label>Статус</label><span>{sm.label}</span></div>
            {/* Балл приоритета 1–5: ставят суперадмины, остальным он виден, только если есть. */}
            {(canScore || priorityScore !== null) && (
              <div className="tv-info-item">
                <label className="tv-info-label-row">
                  Балл
                  <InfoHint side="left" text={scoreHintOf(task)} />
                </label>
                {canScore ? (
                  <PriorityScoreControl
                    value={priorityScore}
                    titleSuffix={awaitsPriorityScore(task) ? ' — задача уйдёт в бэклог' : ''}
                    clearTitleSuffix={unscoreMovesToNew(task, null) ? ' — задача вернётся в «Новую»' : ''}
                    disabled={actionLoadingKey === `board:${task.id}`}
                    onChange={(score) => onSetPriorityScore(task, score)}
                  />
                ) : (
                  <span className="tv-score-value">
                    <PriorityScoreDot score={priorityScore} />
                    {priorityScoreTitle(priorityScore).replace(/^Балл /, '')}
                  </span>
                )}
              </div>
            )}
            {deadlineLabel && (
              <div className="tv-info-item"><label>Дедлайн</label><span className={isTaskOverdue(task) ? 'tv-deadline-chip is-overdue' : ''}>{deadlineLabel}</span></div>
            )}
            <div className="tv-info-item"><label>Повторение</label><span>{task?.recurrence_type ? `${RECURRENCE_OPTIONS.find(item => item.value === task.recurrence_type)?.label || task.recurrence_type}${Number(task?.recurrence_interval || 1) > 1 ? ` · раз в ${task.recurrence_interval}` : ''}` : (task?.is_regulation ? 'Разовый регламент' : '—')}</span></div>
          </div>

          {task.tag === PRODUCTION_TAG && task.production_brief && (
            <>
              <hr className="tv-divider" />
              <ProductionBriefView brief={task.production_brief} />
            </>
          )}

          {/* Ролики и макеты продакшна живут в разделе «Продакшн» (задача #316):
              там их загружают по версиям и там же смотрят перед приёмкой. */}
          {task.tag === PRODUCTION_TAG && typeof onOpenProduction === 'function' && (
            /* Обёртка — чтобы кнопка была по тексту, как «+ Дополнить задачу»,
               а не растягивалась колонкой карточки во всю ширину. */
            <div>
              <button
                type="button"
                className="tv-btn tv-btn-ghost"
                onClick={() => onOpenProduction(task.id)}
              >
                Материалы — в «Продакшне» →
              </button>
            </div>
          )}

          {task.description && (
            <>
              <hr className="tv-divider" />
              <div>
                {/* У продакшна описание — «Описание задания» из брифа. */}
                <p className="tv-block-label">{task.tag === PRODUCTION_TAG ? 'Описание задания' : 'Описание'}</p>
                <p className="tv-description">{task.description}</p>
              </div>
            </>
          )}

          {attachments.length > 0 && (
            <>
              <hr className="tv-divider" />
              <div>
                <p className="tv-block-label">Файлы задачи</p>
                <TaskFileGroup
                  attachments={attachments}
                  photoState={photoState}
                  onOpenPhoto={openPhoto}
                  onOpenFile={canPreviewFiles ? openFile : undefined}
                  onDownload={downloadAttachment}
                />
              </div>
            </>
          )}

          <TaskClarificationsBlock
            task={task}
            messages={messages}
            currentUserId={currentUserId}
            currentUserRole={currentUserRole}
            actionLoadingKey={actionLoadingKey}
            onSubmitMessage={onSubmitTaskMessage}
            onWithdrawRequest={onWithdrawInfoRequest}
            downloadAttachment={downloadAttachment}
            photoState={photoState}
            onOpenPhoto={openPhoto}
            onOpenFile={canPreviewFiles ? openFile : undefined}
          />

          {checklist.length > 0 && (
            <>
              <hr className="tv-divider" />
              <div className="tv-soft-block">
                <p className="tv-block-label">Чек-лист · {progress.done}/{progress.total}</p>
                <div className="tv-checklist">
                  {checklist.map(item => {
                    const loading = actionLoadingKey === `${task.id}:checklist:${item.id}`;
                    return (
                      <div key={item.id} className={`tv-checklist-row ${item.is_done ? 'is-done' : ''}`}>
                        <label className="tv-checklist-line">
                          <input
                            type="checkbox"
                            className="tv-checklist-checkbox"
                            checked={!!item.is_done}
                            disabled={!!loading || !!task?.viewer_is_observer}
                            onChange={() => onToggleChecklistItem?.(task, item, !item.is_done)}
                          />
                          <span className="tv-checklist-title">{item.title}</span>
                        </label>
                        {item.is_done && (
                          <div className="tv-checklist-result">
                            <textarea
                              className="tv-checklist-result-input"
                              defaultValue={item.result_note || ''}
                              placeholder="Итог по пункту…"
                              rows={2}
                              disabled={!!loading}
                              onClick={(event) => event.stopPropagation()}
                              onBlur={(event) => onSaveChecklistNote?.(task, item, event.target.value)}
                            />
                            {item.completed_by_name && (
                              <span className="tv-checklist-result-meta">
                                {item.completed_by_name}{item.completed_at ? ` · ${fmtShortDateTime(item.completed_at)}` : ''}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </>
          )}

          {(reports.length > 0 || compAttachments.length > 0 || canWriteReport) && (
            <>
              <hr className="tv-divider" />
              <TaskReportsBlock
                task={task}
                reports={reports}
                canWrite={canWriteReport}
                currentUserId={currentUserId}
                actionLoadingKey={actionLoadingKey}
                onSubmitReport={onSubmitReport}
                onPatchReport={onPatchReport}
                onRemoveReport={onRemoveReport}
              />
              {compAttachments.length > 0 && (
                <div className="tv-completion-block" style={{ marginTop: 12 }}>
                  <p className="tv-block-label">Файлы результата</p>
                  <TaskFileGroup
                    attachments={compAttachments}
                    photoState={photoState}
                    onOpenPhoto={openPhoto}
                    onOpenFile={canPreviewFiles ? openFile : undefined}
                    onDownload={downloadAttachment}
                    fileBtnStyle={RESULT_FILE_BTN_STYLE}
                  />
                </div>
              )}
            </>
          )}

          {history.length > 0 && (
            <>
              <hr className="tv-divider" />
              <div>
                <p className="tv-block-label">История</p>
                <div className="tv-history-list">
                  {history.map((item, i) => (
                    <div key={i} className={`tv-history-item tv-history-item-${resolveHistorySide(item)}`}>
                      <div className="tv-history-bubble">
                        <span className="tv-history-status">{HISTORY_LABELS[item.status_code] || item.status_code}</span>
                        <span className="tv-history-time">{fmt(item.changed_at)}{item.changed_by_name ? ` · ${item.changed_by_name}` : ''}</span>
                        {item.comment && <span className="tv-history-comment">{item.comment}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        {footerBtns.length > 0 && (
          <div className="tv-drawer-footer otp-modal-footer">
            {/* Подсказка про клавишу — только там, где клавиатура есть. */}
            {!isMobileShell && (
              <span style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--ink-3)' }}>
                <span className="tv-kbd">Esc</span> закрыть
              </span>
            )}
            {footerBtns.map(btn => {
              const key     = `${task.id}:${btn.action}`;
              const loading = actionLoadingKey === key;
              return (
                <button
                  key={btn.action}
                  className={`tv-btn ${btn.cls}`}
                  disabled={!!actionLoadingKey}
                  onClick={() => {
                    if (btn.action === 'completed') { openCompleteModal(task); return; }
                    if (btn.action === 'returned' || btn.action === 'reopened') { openStatusModal(task, btn.action); return; }
                    if (btn.action === 'to_backlog') { onMoveToBacklog?.(task); return; }
                    updateStatus(task.id, btn.action);
                  }}
                >
                  {loading ? 'Сохраняю...' : btn.label}
                </button>
              );
            })}
          </div>
        )}

        {/* Портал в body: над карточкой и на компьютере, и на экране телефона. */}
        <IosLightbox
          url={viewedPhotoUrl}
          alt={viewedPhoto?.file_name || ''}
          onClose={closePhoto}
          counter={photoCount > 1 ? `${photoView.index + 1} из ${photoCount}` : ''}
          onPrev={photoView && photoView.index > 0 ? stepPhoto(-1) : undefined}
          onNext={photoView && photoView.index < photoCount - 1 ? stepPhoto(1) : undefined}
          onDownload={viewedPhoto ? () => downloadAttachment(viewedPhoto) : undefined}
          /* Ссылка истекла, пока карточка была открыта: адреса перезапрашиваются
             один раз (useTaskPhotoPreviews.refresh), и снимок подменяется сам. */
          onError={photoState.refresh}
        />
        {canPreviewFiles && (
          <FilePreviewSheet
            files={fileView ? fileView.items : []}
            index={fileView ? fileView.index : -1}
            onIndexChange={stepFile}
            onClose={closeFile}
            loadFile={loadAttachmentFile}
            cacheKey={taskFileKey}
          />
        )}
      </>
  );

  /* Телефон: карточка — экран с въездом справа и своей записью в истории (жест
     «назад» закрывает её, а не раздел под ней). Выезжающая панель лежала ПОД
     колоколом и поверх бара разделов, а свайп назад менял раздел под ней. */
  if (isMobileShell) {
    return (
      <div className="tv-drawer-screen otp-modal-root" role="dialog" aria-modal="true" aria-label={task.subject || 'Задача'}>
        <div className="tv-drawer otp-modal-panel">
          {drawerInner}
        </div>
        <MobileActionSheet open={actionsOpen} onClose={() => setActionsOpen(false)} actions={sheetActions} />
      </div>
    );
  }

  return (
    <>
      <div className="tv-overlay" onClick={onClose} />
      <div className="tv-drawer">
        {drawerInner}
      </div>
    </>
  );
});

export const PinnedTaskWidget = React.memo(({
  task,
  user,
  showToast,
  apiBaseUrl,
  withAccessTokenHeader,
  availableTasks = [],
  isTasksLoading = false,
  taskRecipients = [],
  isTaskRecipientsLoading = false,
  initialExpanded = false,
  initialPosition = null,
  autoOpenPipRequestId = 0,
  actionLoadingKey,
  onUnpin,
  onOpenDetails,
  onRunAction,
  onDownloadAttachment,
  onToggleChecklistItem,
  onCreateTask,
  onEditTask,
  onDeleteTask,
  onSelectTask,
  onStateChange,
}) => {
  const [expanded, setExpanded] = useState(Boolean(initialExpanded));
  const [position, setPosition] = useState(() => {
    const x = Number(initialPosition?.x);
    const y = Number(initialPosition?.y);
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  });
  const [taskMenuOpen, setTaskMenuOpen] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [quickFormMode, setQuickFormMode] = useState('');
  const [quickForm, setQuickForm] = useState(() => buildEmptyTaskForm());
  const [quickFormFiles, setQuickFormFiles] = useState([]);
  const [quickFormLoading, setQuickFormLoading] = useState(false);
  const [quickFormError, setQuickFormError] = useState('');
  // Сдача задачи из виджета тоже требует отчёта — иначе журнал работ зависел бы от того, откуда нажали.
  const [completeDraft, setCompleteDraft] = useState(null);
  const [taskMenuScope, setTaskMenuScope] = useState('incoming');
  const notifyNote = useCallback((message, type = 'success') => {
    if (typeof showToast === 'function') showToast(message, type);
  }, [showToast]);
  const buildNoteHeaders = useCallback((extra = {}) => {
    const headers = { ...extra };
    if (user?.id) headers['X-User-Id'] = String(user.id);
    return typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(headers) : headers;
  }, [user?.id, withAccessTokenHeader]);
  const notesState = useTaskNotes({
    userId: user?.id,
    apiBaseUrl,
    buildHeaders: buildNoteHeaders,
    notify: notifyNote,
  });
  /* Файлы задачи открываются в окне просмотра, как в карточке. Виджет в окне
     «картинка в картинке» (Chrome) — исключение: окно просмотра в нём не
     помещается, а в окне портала его не видно, если тот свёрнут, — там файл
     по-прежнему скачивается. */
  const [fileView, setFileView] = useState(null);
  const loadPinnedFile = useCallback(async (att, signal) => {
    const res = await axios.get(
      `${apiBaseUrl}/api/tasks/attachments/${att.id}/download`,
      { headers: buildNoteHeaders(), responseType: 'blob', signal }
    );
    return res.data;
  }, [apiBaseUrl, buildNoteHeaders]);
  const [paletteId, setPaletteId] = useState(() => {
    if (typeof window === 'undefined') return PIN_PALETTES[0].id;
    // Пока человек сам не выбирал оформление — как у сайта: при тёмной теме
    // портала светлая панель была бы единственным светлым пятном на экране.
    const siteIsDark = document.documentElement?.getAttribute?.('data-otp-theme') === 'dark';
    return readPinPaletteChoice() || (siteIsDark ? 'midnight' : PIN_PALETTES[0].id);
  });
  const [selectedMenuPersonKey, setSelectedMenuPersonKey] = useState(null);
  const [pipContainer, setPipContainer] = useState(null);
  const [pipWindow, setPipWindow] = useState(null);
  const openPinnedFile = (items, att) => {
    if (pipWindow) {
      onDownloadAttachment?.(att);
      return;
    }
    const index = items.indexOf(att);
    if (index >= 0) setFileView({ items, index });
  };
  // Другая задача в виджете или виджет ушёл в отдельное окно — просмотр закрывается.
  useEffect(() => { setFileView(null); }, [task?.id, pipWindow]);
  const [isDragging, setIsDragging] = useState(false);
  const widgetRef = useRef(null);
  const dragStateRef = useRef(null);
  const latestPositionRef = useRef(position);
  const currentUserRole = normalizeRole(user?.role);
  const currentUserId = Number(user?.id || 0);
  const sm = STATUS_META[task?.status] || { label: task?.status || '—', badge: 'tv-badge-gray' };
  const tm = TAG_META[task?.tag] || { label: task?.tag || '—', badge: 'tv-badge-gray' };
  const pm = PRIORITY_META[task?.priority] || PRIORITY_META.normal;
  // Как и в карточке: файл уточнения живёт в уточнении, а не в «Файлах задачи».
  const attachments = (Array.isArray(task?.attachments) ? task.attachments : [])
    .filter((att) => !att?.message_id);
  const compAttachments = Array.isArray(task?.completion_attachments) ? task.completion_attachments : [];
  const checklist = Array.isArray(task?.checklist) ? task.checklist : [];
  const progress = checklistProgress(checklist);
  const activePalette = PIN_PALETTES.find((item) => item.id === paletteId) || PIN_PALETTES[0];
  const canUseDocumentPictureInPicture = typeof window !== 'undefined' && Boolean(window.documentPictureInPicture?.requestWindow);
  // Виджет ведёт задачу по статусам; планирование (в бэклог) — дело раздела.
  const actionButtons = useMemo(
    () => buildTaskActionButtons(task, currentUserId, currentUserRole)
      .filter((btn) => !['edit', 'delete', 'to_backlog'].includes(btn.action)),
    [task, currentUserId, currentUserRole]
  );
  const managementButtons = useMemo(
    () => buildTaskActionButtons(task, currentUserId, currentUserRole)
      .filter((btn) => ['edit', 'delete'].includes(btn.action)),
    [task, currentUserId, currentUserRole]
  );
  const canEditTask = managementButtons.some((btn) => btn.action === 'edit');
  const canDeleteTask = managementButtons.some((btn) => btn.action === 'delete');
  const quickAssigneeSelectId = useId();
  const recipientOptions = useMemo(() => {
    const unique = new Map();
    const addRecipient = (person) => {
      const id = Number(person?.id || 0);
      if (!id || unique.has(id)) return;
      unique.set(id, person);
    };
    (Array.isArray(taskRecipients) ? taskRecipients : []).forEach(addRecipient);
    // Исполнители задачи обязаны быть в списке, даже если их уже нет в
    // получателях: иначе правка молча выкинула бы человека из состава.
    taskAssignees(task).forEach(addRecipient);
    return Array.from(unique.values());
  }, [taskRecipients, task]);
  /* Исполнители доски в быстрой форме (26.11.2026, находка разбора): задачу доски
     поручают только её исполнителям. Состав досок App не грузит, поэтому доску
     закреплённой задачи спрашиваем, только когда открыли форму, — один запрос на
     открытие; пока он в пути, поле исполнителей «грузится». Не ответил — форма
     прежняя: участника сервер всё равно не пустит и скажет почему. */
  const boardProjectId = task?.project?.id ? String(task.project.id) : '';
  const [quickBoard, setQuickBoard] = useState(null);
  const [quickBoardLoading, setQuickBoardLoading] = useState(false);
  useEffect(() => {
    if (!quickFormMode || !boardProjectId) {
      setQuickBoard(null);
      setQuickBoardLoading(false);
      return undefined;
    }
    let cancelled = false;
    setQuickBoardLoading(true);
    axios.get(`${apiBaseUrl}/api/tasks/projects`, { headers: buildNoteHeaders() })
      .then((res) => {
        if (cancelled) return;
        const project = findProject(res?.data?.projects, boardProjectId);
        const bound = project && hasBoardPeople(project) ? project : null;
        setQuickBoard(bound);
        // Связанная задача — тем из прежнего состава, кто исполнитель доски.
        if (bound && quickFormMode === 'create') {
          setQuickForm((prev) => ({ ...prev, assigneeIds: keepAssigneesForProject(prev.assigneeIds, bound) }));
        }
      })
      .catch(() => { if (!cancelled) setQuickBoard(null); })
      .finally(() => { if (!cancelled) setQuickBoardLoading(false); });
    return () => { cancelled = true; };
  }, [quickFormMode, boardProjectId, apiBaseUrl, buildNoteHeaders]);
  /* Кого предлагать: у доски с людьми — её исполнителей и тех, кто уже в составе
     (их можно снять), иначе — всех получателей, как раньше. */
  const quickAssigneePeople = useMemo(() => {
    if (!quickBoard) return recipientOptions;
    const byId = new Map(boardAssignees(quickBoard).map((person) => [String(person.id), person]));
    const members = Array.isArray(quickBoard.members) ? quickBoard.members : [];
    (quickForm.assigneeIds || []).forEach((id) => {
      const key = String(id);
      if (byId.has(key)) return;
      const known = recipientOptions.find((person) => String(person.id) === key)
        || members.find((person) => String(person.id) === key);
      if (known) byId.set(key, known);
    });
    return [...byId.values()];
  }, [quickBoard, recipientOptions, quickForm.assigneeIds]);
  const assigneeSelectOptions = useMemo(
    () => quickAssigneePeople.map((person) => ({
      value: String(person.id),
      label: person.role
        ? `${person.name} (${recipientRoleLabel(person)})`
        : person.name,
    })),
    [quickAssigneePeople]
  );
  const quickNoExecutors = boardWithoutExecutors(quickBoard);
  const taskOptions = useMemo(() => {
    const unique = new Map();
    [...availableTasks, task].filter(Boolean).forEach((item) => {
      const id = Number(item?.id || 0);
      if (!id || unique.has(id)) return;
      unique.set(id, item);
    });
    return Array.from(unique.values()).sort((a, b) => {
      const aTs = new Date(a?.created_at || 0).getTime() || 0;
      const bTs = new Date(b?.created_at || 0).getTime() || 0;
      return bTs - aTs;
    });
  }, [availableTasks, task]);
  const incomingTaskOptions = useMemo(
    () => taskOptions.filter((item) => isTaskAssignee(item, currentUserId)),
    [currentUserId, taskOptions]
  );
  const outgoingTaskOptions = useMemo(
    () => taskOptions.filter((item) => Number(item?.creator?.id || 0) === currentUserId),
    [currentUserId, taskOptions]
  );
  const incomingMenuGroups = useMemo(
    () => groupTasksByPerson(incomingTaskOptions, (item) => item?.creator),
    [incomingTaskOptions]
  );
  const outgoingMenuGroups = useMemo(
    () => groupTasksByPerson(outgoingTaskOptions, (item) => taskAssignees(item)),
    [outgoingTaskOptions]
  );
  const taskMenuGroups = taskMenuScope === 'outgoing' ? outgoingMenuGroups : incomingMenuGroups;
  const activeTaskMenuGroup = useMemo(
    () => taskMenuGroups.find((group) => group.key === selectedMenuPersonKey) || taskMenuGroups[0] || null,
    [selectedMenuPersonKey, taskMenuGroups]
  );

  const clampPosition = useCallback((nextX, nextY) => {
    const node = widgetRef.current;
    const rect = node?.getBoundingClientRect();
    const width = rect?.width || 360;
    const height = rect?.height || 220;
    const maxX = Math.max(8, window.innerWidth - width - 8);
    const maxY = Math.max(8, window.innerHeight - height - 8);
    return {
      x: Math.min(Math.max(8, nextX), maxX),
      y: Math.min(Math.max(8, nextY), maxY),
    };
  }, []);

  useEffect(() => {
    if (!task?.id || typeof window === 'undefined') return;
    const node = widgetRef.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    setPosition((prev) => {
      const next = prev
        ? clampPosition(prev.x, prev.y)
        : clampPosition(
            window.innerWidth - rect.width - 18,
            window.innerHeight - rect.height - 18
          );
      latestPositionRef.current = next;
      onStateChange?.({ expanded, position: next });
      return next;
    });
  }, [task?.id, expanded, clampPosition, onStateChange]);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const handleResize = () => {
      setPosition((prev) => {
        if (!prev) return prev;
        const next = clampPosition(prev.x, prev.y);
        latestPositionRef.current = next;
        onStateChange?.({ expanded, position: next });
        return next;
      });
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [clampPosition, expanded, onStateChange]);

  const handlePointerDown = useCallback((event) => {
    if (event.button !== 0 || !widgetRef.current) return;
    // Панель тянется за всю шапку, как окно macOS, — кроме кнопок в ней.
    if (event.target?.closest?.('button, a, input, textarea, select, [role="menu"]')) return;
    const rect = widgetRef.current.getBoundingClientRect();
    dragStateRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }, []);

  const handlePointerMove = useCallback((event) => {
    const drag = dragStateRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    // Щелчок по шапке — ещё не перенос: без порога панель вздрагивала тенью.
    if (!drag.moved) {
      if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 3) return;
      drag.moved = true;
      setIsDragging(true);
    }
    const next = clampPosition(
      event.clientX - drag.offsetX,
      event.clientY - drag.offsetY
    );
    latestPositionRef.current = next;
    setPosition(next);
  }, [clampPosition]);

  const handlePointerEnd = useCallback((event) => {
    const drag = dragStateRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragStateRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    if (!drag.moved) return;
    setIsDragging(false);
    onStateChange?.({
      expanded,
      position: latestPositionRef.current,
    });
  }, [expanded, onStateChange]);

  /* В окне «поверх окон» «Свернуть» уменьшает само окно до шапки, заголовка и
     кнопок: содержимое сворачивалось, а окно оставалось прежним — под кнопками
     зияла пустая серая половина. «Развернуть» возвращает прежнюю высоту.
     Менять размер окно PiP даёт только сразу после щелчка — отсюда и зовём. */
  const pipExpandedHeightRef = useRef(0);
  // Окно сейчас ужато нами. Пока так, «развернуть» извне (закрепили другую
  // задачу на странице) не принимаем: без щелчка в самом окне его не увеличить,
  // а развёрнутое содержимое в окне-полоске видно было бы одну шапку.
  const pipCollapsedRef = useRef(false);
  const fitPipWindow = useCallback((nextExpanded) => {
    const win = pipWindow;
    if (!win || win.closed) return;
    try {
      if (nextExpanded) {
        if (!pipCollapsedRef.current) return;
        pipCollapsedRef.current = false;
        if (pipExpandedHeightRef.current > win.outerHeight) {
          win.resizeTo(win.outerWidth, pipExpandedHeightRef.current);
        }
        return;
      }
      // Высоту развёрнутого окна помним одну: второе «Свернуть» подряд не
      // должно записать вместо неё высоту полоски.
      if (!pipCollapsedRef.current) pipExpandedHeightRef.current = win.outerHeight;
      pipCollapsedRef.current = true;
      win.requestAnimationFrame(() => {
        const node = widgetRef.current;
        const scroll = node?.querySelector('.tv-pin-scroll');
        if (!node || !scroll || win.closed) return;
        const style = win.getComputedStyle(scroll);
        const hero = scroll.firstElementChild;
        const header = node.querySelector('.tv-pin-header');
        const footer = node.querySelector('.tv-pin-footer');
        const content = (header?.offsetHeight || 0)
          + (hero?.offsetHeight || 0)
          + (parseFloat(style.paddingTop) || 0)
          + (parseFloat(style.paddingBottom) || 0)
          + (footer?.offsetHeight || 0);
        const frame = Math.max(0, win.outerHeight - win.innerHeight);
        if (content > 0) win.resizeTo(win.outerWidth, Math.ceil(content + frame));
      });
    } catch (error) {
      // Окно не дало сменить размер — содержимое всё равно свёрнуто.
    }
  }, [pipWindow]);

  const toggleExpanded = useCallback(() => {
    const next = !expanded;
    setExpanded(next);
    onStateChange?.({
      expanded: next,
      position: latestPositionRef.current,
    });
    fitPipWindow(next);
  }, [expanded, fitPipWindow, onStateChange]);

  useEffect(() => {
    latestPositionRef.current = position;
  }, [position]);

  useEffect(() => {
    setExpanded(pipCollapsedRef.current ? false : Boolean(initialExpanded));
  }, [initialExpanded, task?.id]);

  // Выбор запоминаем только когда его сделал человек: оформление «как у сайта»
  // не записывается, иначе после смены темы сайта панель осталась бы в прежней.
  const choosePalette = useCallback((id) => {
    setPaletteId(id);
    try {
      window.localStorage.setItem(PIN_PALETTE_STORAGE_KEY, id);
    } catch (error) {
      // Palette preference is best-effort browser state.
    }
  }, []);

  useEffect(() => {
    if (!pipWindow) return;
    pipWindow.document.body.style.background = activePalette.vars['--bg'] || '#f5f5f7';
  }, [pipWindow, activePalette]);

  const openQuickCreate = useCallback(() => {
    setQuickForm(buildEmptyTaskForm({
      assigneeIds: formAssigneeIds(task, currentUserId),
      priority: task?.priority || 'normal',
      // Связанная задача — на ту же доску (задача #401). Нет доступа — сервер
      // откажет понятным текстом, и задачу поставят из раздела.
      projectId: task?.project?.id ? String(task.project.id) : '',
    }));
    setQuickFormFiles([]);
    setQuickFormError('');
    setQuickFormMode('create');
    setExpanded(true);
    onStateChange?.({ expanded: true, position: latestPositionRef.current });
    if (!expanded) fitPipWindow(true);
    setNoteOpen(false);
    setPaletteOpen(false);
    setTaskMenuOpen(false);
  }, [currentUserId, expanded, fitPipWindow, onStateChange, task, task?.priority]);

  const openQuickEdit = useCallback(() => {
    if (!task?.id) return;
    setQuickForm(taskToTaskForm(task, currentUserId));
    setQuickFormFiles([]);
    setQuickFormError('');
    setQuickFormMode('edit');
    setExpanded(true);
    onStateChange?.({ expanded: true, position: latestPositionRef.current });
    if (!expanded) fitPipWindow(true);
    setNoteOpen(false);
    setPaletteOpen(false);
    setTaskMenuOpen(false);
  }, [currentUserId, expanded, fitPipWindow, onStateChange, task]);

  const closeQuickForm = useCallback(() => {
    setQuickFormMode('');
    setQuickFormFiles([]);
    setQuickFormError('');
  }, []);

  const submitQuickForm = useCallback(async (event) => {
    event?.preventDefault?.();
    if (!quickFormMode) return;
    if (!String(quickForm.subject || '').trim()) {
      setQuickFormError('Укажите тему задачи');
      return;
    }
    if (!(quickForm.assigneeIds || []).length) {
      setQuickFormError('Выберите исполнителя');
      return;
    }
    const [pastDeadline] = deadlineIssues(quickForm);
    if (pastDeadline) {
      setQuickFormError(pastDeadline.text);
      return;
    }
    const payload = buildTaskJsonPayload(quickForm);
    setQuickFormLoading(true);
    setQuickFormError('');
    try {
      const nextTask = quickFormMode === 'edit'
        ? await onEditTask?.(task, payload)
        : await onCreateTask?.(payload, quickFormFiles);
      if (nextTask?.id) onSelectTask?.(nextTask);
      setQuickFormFiles([]);
      setQuickFormMode('');
    } catch (error) {
      setQuickFormError(error?.response?.data?.error || error?.message || 'Не удалось сохранить задачу');
    } finally {
      setQuickFormLoading(false);
    }
  }, [onCreateTask, onEditTask, onSelectTask, quickForm, quickFormFiles, quickFormMode, task]);

  const handleQuickDelete = useCallback(async () => {
    if (!task?.id || !onDeleteTask) return;
    const confirmHost = pipWindow || window;
    if (!confirmHost.confirm('Удалить эту задачу?')) return;
    setQuickFormLoading(true);
    try {
      await onDeleteTask(task);
      setQuickFormMode('');
    } finally {
      setQuickFormLoading(false);
    }
  }, [onDeleteTask, pipWindow, task]);

  const updateQuickFormField = useCallback((field, value) => {
    setQuickForm((prev) => ({ ...prev, [field]: value }));
    setQuickFormError('');
  }, []);

  const updateQuickFormChecklist = useCallback((items) => {
    setQuickForm((prev) => ({ ...prev, checklistItems: items }));
    setQuickFormError('');
  }, []);

  const updateQuickFormFiles = useCallback((files) => {
    setQuickFormFiles(filesFromList(files));
    setQuickFormError('');
  }, []);

  useEffect(() => {
    if (!taskMenuGroups.length) {
      setSelectedMenuPersonKey(null);
      return;
    }
    setSelectedMenuPersonKey((prev) => {
      if (prev && taskMenuGroups.some((group) => group.key === prev)) return prev;
      // В исходящих группа — это исполнитель, а их может быть несколько:
      // подойдёт группа любого из них.
      const currentPersonIds = taskMenuScope === 'outgoing'
        ? taskAssignees(task).map((person) => Number(person.id))
        : [Number(task?.creator?.id || 0)];
      const matchingGroup = taskMenuGroups.find(
        (group) => currentPersonIds.includes(Number(group?.personId || 0))
      );
      return matchingGroup?.key || taskMenuGroups[0].key;
    });
  }, [task, task?.creator?.id, taskMenuGroups, taskMenuScope]);

  useEffect(() => {
    if (!pipWindow) return undefined;
    const handlePageHide = () => {
      pipCollapsedRef.current = false;
      pipExpandedHeightRef.current = 0;
      setPipWindow(null);
      setPipContainer(null);
    };
    pipWindow.addEventListener('pagehide', handlePageHide);
    return () => pipWindow.removeEventListener('pagehide', handlePageHide);
  }, [pipWindow]);

  useEffect(() => () => {
    try {
      pipWindow?.close?.();
    } catch (error) {
      // Ignore browser shutdown races while closing the widget.
    }
  }, [pipWindow]);

  const openDocumentPictureInPicture = useCallback(async () => {
    // Окно уже открыто и ужато — оставляем свёрнутым: увеличит его щелчок
    // «Развернуть» в самом окне (см. fitPipWindow).
    if (!(pipWindow && pipCollapsedRef.current)) {
      setExpanded(true);
      onStateChange?.({
        expanded: true,
        position: latestPositionRef.current,
      });
    }
    if (pipWindow) {
      pipWindow.focus?.();
      return;
    }
    if (!canUseDocumentPictureInPicture) return;
    try {
      const nextPipWindow = await window.documentPictureInPicture.requestWindow({
        width: 420,
        height: 520,
      });
      nextPipWindow.document.title = 'Закрепленная задача';
      cloneDocumentStyles(nextPipWindow);
      nextPipWindow.document.body.style.margin = '0';
      nextPipWindow.document.body.style.background = activePalette.vars['--bg'] || '#f5f5f7';
      nextPipWindow.document.body.style.minHeight = '100vh';
      const root = nextPipWindow.document.createElement('div');
      nextPipWindow.document.body.appendChild(root);
      setPipWindow(nextPipWindow);
      setPipContainer(root);
    } catch (error) {
      // The browser can reject PiP if it is unavailable or not user-triggered.
    }
  }, [activePalette.vars, canUseDocumentPictureInPicture, onStateChange, pipWindow]);

  useEffect(() => {
    if (!autoOpenPipRequestId) return;
    openDocumentPictureInPicture();
  }, [autoOpenPipRequestId, openDocumentPictureInPicture]);

  // Меню «Ещё» в шапке. Своё, а не IosMenu: общее меню рисуется порталом в
  // документ портала, и в окне «поверх окон» его не было бы видно.
  const [moreOpen, setMoreOpen] = useState(false);
  const [moreUp, setMoreUp] = useState(false);
  const moreButtonRef = useRef(null);
  const moreMenuRef = useRef(null);
  const peopleStripRef = useRef(null);
  const quickFormId = useId();
  // Клавиатура: меню, открытое с клавиатуры, сразу ставит фокус на первый пункт,
  // а после «‹ Задача» фокус возвращается на кнопку, которая открыла экран.
  const moreFocusFirstRef = useRef(false);
  const returnFocusRef = useRef('');
  const lastHeightRef = useRef(0);
  const pinView = noteOpen
    ? 'notes'
    : quickFormMode
      ? 'form'
      : paletteOpen
        ? 'palette'
        : taskMenuOpen
          ? 'list'
          : 'task';

  useEffect(() => {
    const node = widgetRef.current;
    if (!node) return;
    if (pinView !== 'task') {
      // Кнопка, открывшая экран, ушла вместе со старой шапкой — фокус упал на
      // body. Ставим его на «‹ Задача» («Отмена» у формы); после щелчка мышью
      // программный фокус кольца не рисует, с клавиатуры — продолжаем отсюда.
      const doc = node.ownerDocument;
      if (!doc.activeElement || doc.activeElement === doc.body) {
        node.querySelector('.tv-pin-header .tv-pin-back, .tv-pin-header .tv-pin-text-btn')?.focus?.({ preventScroll: true });
      }
      return;
    }
    if (!returnFocusRef.current) return;
    const key = returnFocusRef.current;
    returnFocusRef.current = '';
    node.querySelector(`[data-pin-tool="${key}"]`)?.focus?.({ preventScroll: true });
  }, [pinView]);

  /* В «Моих задачах» выбранный человек мог остаться за краем ленты (после
     переключения «Входящие / Исходящие» лента стоит в начале) — подводим к нему. */
  const activeMenuGroupKey = activeTaskMenuGroup?.key || '';
  useEffect(() => {
    if (!taskMenuOpen || !activeMenuGroupKey) return;
    const strip = peopleStripRef.current;
    const chip = strip?.querySelector('.tv-pin-person.is-active');
    if (!strip || !chip) return;
    const stripRect = strip.getBoundingClientRect();
    const chipRect = chip.getBoundingClientRect();
    if (chipRect.left < stripRect.left + 12) {
      strip.scrollLeft -= stripRect.left + 12 - chipRect.left;
    } else if (chipRect.right > stripRect.right - 12) {
      strip.scrollLeft += chipRect.right - (stripRect.right - 12);
    }
  }, [taskMenuOpen, taskMenuScope, activeMenuGroupKey]);

  useEffect(() => { setMoreOpen(false); }, [task?.id, pipWindow]);

  useEffect(() => {
    if (!moreOpen) return undefined;
    const doc = widgetRef.current?.ownerDocument || document;
    if (moreFocusFirstRef.current) {
      moreFocusFirstRef.current = false;
      moreMenuRef.current?.querySelector('[role="menuitem"]:not(:disabled)')?.focus?.();
    }
    const handlePointer = (event) => {
      if (moreMenuRef.current?.contains(event.target) || moreButtonRef.current?.contains(event.target)) return;
      setMoreOpen(false);
    };
    const handleKey = (event) => {
      // Esc и Tab закрывают меню и возвращают фокус на «⋯», как у меню macOS:
      // иначе Tab из последнего пункта уводил фокус за пределы панели.
      if (event.key === 'Escape' || (event.key === 'Tab' && moreMenuRef.current?.contains(doc.activeElement))) {
        event.preventDefault();
        event.stopPropagation();
        setMoreOpen(false);
        moreButtonRef.current?.focus?.();
        return;
      }
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const items = Array.from(moreMenuRef.current?.querySelectorAll('[role="menuitem"]:not(:disabled)') || []);
      if (!items.length) return;
      event.preventDefault();
      const current = items.indexOf(doc.activeElement);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      const next = current < 0 ? (step > 0 ? 0 : items.length - 1) : (current + step + items.length) % items.length;
      items[next].focus();
    };
    doc.addEventListener('pointerdown', handlePointer, true);
    doc.addEventListener('keydown', handleKey, true);
    return () => {
      doc.removeEventListener('pointerdown', handlePointer, true);
      doc.removeEventListener('keydown', handleKey, true);
    };
  }, [moreOpen]);

  /* Панель на странице меняет высоту (свернули, открыли «Мои задачи»). Стоит она
     по левому верхнему углу, поэтому выросшая у нижнего края уходила бы за экран —
     подтягиваем её обратно, как при смене размера окна. */
  useEffect(() => {
    if (pipWindow || typeof ResizeObserver === 'undefined') return undefined;
    const node = widgetRef.current;
    if (!node) return undefined;
    lastHeightRef.current = node.getBoundingClientRect().height;
    const observer = new ResizeObserver(() => {
      const height = node.getBoundingClientRect().height;
      const before = lastHeightRef.current;
      lastHeightRef.current = height;
      if (dragStateRef.current) return;
      setPosition((prev) => {
        if (!prev) return prev;
        // Панель, прижатая к низу окна, при смене высоты остаётся прижатой (как
        // свёрнутая панель macOS), а не повисает над краем на высоту разницы.
        const gap = window.innerHeight - (prev.y + before);
        const y = before > 0 && gap >= 0 && gap <= 32 ? window.innerHeight - gap - height : prev.y;
        const next = clampPosition(prev.x, y);
        if (next.x === prev.x && next.y === prev.y) return prev;
        latestPositionRef.current = next;
        return next;
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [pipWindow, clampPosition, task?.id]);

  if (!task?.id) return null;

  const view = pinView;
  const showView = (name, returnTo = '') => {
    setMoreOpen(false);
    if (name !== 'task') returnFocusRef.current = returnTo || name;
    setNoteOpen(name === 'notes');
    setPaletteOpen(name === 'palette');
    setTaskMenuOpen(name === 'list');
    // Свёрнутое окно «поверх окон» мало для списка и заметок — возвращаем ему высоту.
    if (name !== 'task' && pipWindow && !expanded) {
      setExpanded(true);
      onStateChange?.({ expanded: true, position: latestPositionRef.current });
      fitPipWindow(true);
    }
  };
  const moreItemsCount = 3
    + (canEditTask && onEditTask ? 1 : 0)
    + (!pipWindow && canUseDocumentPictureInPicture ? 1 : 0)
    + (canDeleteTask && onDeleteTask ? 1 : 0);
  const toggleMore = (event) => {
    if (moreOpen) {
      setMoreOpen(false);
      return;
    }
    // В ужатом окне «поверх окон» меню некуда раскрыться (вверху край окна) —
    // сначала возвращаем окну высоту, щелчок даёт на это право.
    if (pipWindow && !expanded) {
      setExpanded(true);
      onStateChange?.({ expanded: true, position: latestPositionRef.current });
      fitPipWindow(true);
    }
    // Меню раскрывается вниз, а у нижнего края экрана — вверх, как меню macOS.
    const button = moreButtonRef.current;
    const host = button?.ownerDocument?.defaultView || window;
    const rect = button?.getBoundingClientRect?.();
    const needed = moreItemsCount * 34 + 9 + 14;
    if (rect && !pipWindow) {
      const below = host.innerHeight - rect.bottom;
      setMoreUp(below < needed && rect.top > below);
    } else {
      setMoreUp(false);
    }
    moreFocusFirstRef.current = event?.detail === 0;
    setMoreOpen(true);
  };
  const runMenuAction = (action) => {
    setMoreOpen(false);
    action();
  };

  const taskKey = taskKeyOf(task);
  const assignees = taskAssignees(task);
  const statusTone = pinToneOf(sm.badge) || 'gray';
  const tagTone = pinToneOf(tm.badge) || 'gray';
  const priorityTone = task?.priority && task.priority !== 'normal' ? (pinToneOf(pm.badge) || 'gray') : '';
  const overdue = isTaskOverdue(task);
  const dragProps = pipWindow ? {} : {
    onPointerDown: handlePointerDown,
    onPointerMove: handlePointerMove,
    onPointerUp: handlePointerEnd,
    onPointerCancel: handlePointerEnd,
  };
  const backButton = (
    <button type="button" className="tv-pin-back" onClick={() => showView('task')}>
      <LucideChevronLeft size={17} strokeWidth={2.2} />
      Задача
    </button>
  );
  /* У каждого экрана своя шапка и своё содержимое (key): иначе React переиспользовал
     бы узлы — фокус с «Мои задачи» оставался на появившейся «+ Новая» (второе
     нажатие Enter создавало задачу), а прокрутка одного экрана переезжала в другой. */
  const navHeader = ({ left = backButton, title, sub = '', right = null }) => (
    <header key={`head-${view}`} className="tv-pin-header is-nav" {...dragProps}>
      <div className="tv-pin-nav-side">{left}</div>
      <div className="tv-pin-nav-center">
        <span className="tv-pin-nav-title">{title}</span>
        {sub && <span className="tv-pin-nav-sub">{sub}</span>}
      </div>
      <div className="tv-pin-nav-side is-end">{right}</div>
    </header>
  );
  const fileRows = (items) => items.map((att) => {
    const { kind, ext } = previewKind(att);
    return (
      <button
        key={att.id}
        type="button"
        className="tv-pin-file"
        // В окне «поверх окон» просмотр не помещается — файл скачивается, и
        // значок честно говорит об этом вместо шеврона «перейти».
        title={pipWindow ? `Скачать: ${att.file_name}` : att.file_name}
        onClick={() => openPinnedFile(items, att)}
      >
        <TypeBadge ext={ext} kind={kind} />
        <span className="tv-pin-file-name">{att.file_name}</span>
        {pipWindow
          ? <Download className="tv-pin-chevron" size={15} strokeWidth={2} aria-hidden="true" />
          : <LucideChevronRight className="tv-pin-chevron" size={15} strokeWidth={2} aria-hidden="true" />}
      </button>
    );
  });

  const quickTaskForm = quickFormMode ? (
    <form id={quickFormId} className="tv-pin-form" onSubmit={submitQuickForm}>
      <div className="tv-form-field">
        <label>Тема *</label>
        <input
          className="tv-input"
          value={quickForm.subject}
          maxLength={255}
          disabled={quickFormLoading}
          placeholder="Введите тему задачи"
          onChange={(event) => updateQuickFormField('subject', event.target.value)}
        />
      </div>
      <div className="tv-form-field">
        <label>Описание</label>
        <textarea
          className="tv-textarea"
          value={quickForm.description}
          disabled={quickFormLoading}
          onChange={(event) => updateQuickFormField('description', event.target.value)}
        />
      </div>
      {/* Тип и срочность — сегментами во всю ширину, как в форме задачи: в узком
          окне две колонки с сегментами не помещались бы. */}
      <div className="tv-form-field">
        <label>Тип</label>
        <div className="tv-composer-segmented is-stretch" role="group" aria-label="Тип задачи">
          {/* Брифа в быстрой форме нет, поэтому продакшн здесь не ставится — только
              показывается, если закреплённая задача уже этого типа. */}
          {(quickForm.tag === PRODUCTION_TAG ? TAG_OPTIONS : BASIC_TAG_OPTIONS).map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={quickForm.tag === item.value}
              className={quickForm.tag === item.value ? 'is-active' : ''}
              disabled={quickFormLoading}
              onClick={() => updateQuickFormField('tag', item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div className="tv-form-field">
        <label>Срочность</label>
        <div className="tv-composer-segmented is-stretch" role="group" aria-label="Срочность">
          {PRIORITY_OPTIONS.map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={quickForm.priority === item.value}
              className={quickForm.priority === item.value ? 'is-active' : ''}
              disabled={quickFormLoading}
              onClick={() => updateQuickFormField('priority', item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div className="tv-form-field">
        <label htmlFor={quickAssigneeSelectId}>
          {(quickForm.assigneeIds || []).length > 1 ? 'Исполнители *' : 'Исполнитель *'}
        </label>
        <AssigneeField
          id={quickAssigneeSelectId}
          value={quickForm.assigneeIds || []}
          options={assigneeSelectOptions}
          people={quickBoard ? [...recipientOptions, ...quickAssigneePeople] : recipientOptions}
          onChange={(next) => updateQuickFormField('assigneeIds', next)}
          disabled={quickFormLoading}
          isLoading={isTaskRecipientsLoading || quickBoardLoading}
          placeholder={quickNoExecutors
            ? 'У доски нет исполнителей'
            : (quickBoard ? 'Исполнитель доски' : 'Выберите сотрудника')}
        />
      </div>
      <div className="tv-form-field">
        <label>Дедлайн</label>
        {/* У повторяющейся — срок ближайшего повтора (см. repeatAnchorOf); «i» здесь
            нет: подсказка рисуется в главном окне, а форма живёт и в окне PiP. */}
        <TaskDeadlineField
          value={quickForm.dueAt}
          disabled={quickFormLoading}
          presets={!repeatAnchorOf(quickForm)}
          fromDate={splitLocalDateTime(repeatAnchorOf(quickForm)).date}
          onChange={(next) => updateQuickFormField('dueAt', next || '')}
        />
      </div>
      {/* У повтора регламента (итерации) расписания нет — оно у шаблона. */}
      {!quickForm.regulationParentId && (
      <div className="tv-soft-block">
        {/* Да/нет — переключателем iOS, как в настройках, а не системной галочкой. */}
        <label className="tv-pin-switch-row">
          <span>Регламентная задача</span>
          <IosToggle
            checked={!!quickForm.isRegulation}
            disabled={quickFormLoading}
            onChange={(checked) => setQuickForm((prev) => ({
              ...prev,
              isRegulation: checked,
              recurrenceType: checked ? (prev.recurrenceType || 'daily') : ''
            }))}
          />
        </label>
        {quickForm.isRegulation && (
          <div className="tv-pin-form-grid" style={{ marginTop: 12 }}>
            <div className="tv-form-field" style={{ gridColumn: '1 / -1' }}>
              <label>Период</label>
              <div className="tv-composer-segmented is-stretch" role="group" aria-label="Период повтора">
                {RECURRENCE_OPTIONS.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    aria-pressed={(quickForm.recurrenceType || 'daily') === item.value}
                    className={(quickForm.recurrenceType || 'daily') === item.value ? 'is-active' : ''}
                    disabled={quickFormLoading}
                    onClick={() => updateQuickFormField('recurrenceType', item.value)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="tv-form-field">
              <label>Интервал</label>
              <input
                className="tv-input"
                type="number"
                min="1"
                max="365"
                value={quickForm.recurrenceInterval}
                disabled={quickFormLoading}
                onChange={(event) => updateQuickFormField('recurrenceInterval', event.target.value)}
              />
            </div>
          </div>
        )}
      </div>
      )}
      {/* Чек-лист задачи ещё неизвестен (слепок без него, до синхронизации) — его не
          правят: присланный список сервер принял бы за весь чек-лист. */}
      {quickForm.initialChecklist !== null && (
        <div className="tv-form-field">
          <label>Чек-лист</label>
          <ChecklistDraftEditor
            compact
            items={quickForm.checklistItems}
            disabled={quickFormLoading}
            onChange={updateQuickFormChecklist}
          />
        </div>
      )}
      {quickFormMode === 'create' ? (
        <div className="tv-form-field">
          <label>Вложения</label>
          <TaskFileDropzone
            files={quickFormFiles}
            disabled={quickFormLoading}
            onChange={updateQuickFormFiles}
          />
        </div>
      ) : attachments.length > 0 ? (
        <div className="tv-pin-section">
          <div className="tv-pin-section-head"><span>Прикрепленные файлы</span></div>
          <div className="tv-pin-group">{fileRows(attachments)}</div>
        </div>
      ) : null}
    </form>
  ) : null;

  let header;
  let body;
  let footer = null;

  if (view === 'notes') {
    header = navHeader({
      title: 'Заметки',
      // Подпись — только когда есть о чём предупредить: обычное «сохраняется» — шум.
      sub: notesState?.backendReady ? '' : 'Сервер недоступен — локальный режим',
    });
    body = (
      <div key="body-notes" className="tv-pin-scroll is-notes">
        <TaskNotesPanel notesState={notesState} compact fullScreen={!!pipWindow} hideHeading />
      </div>
    );
  } else if (view === 'form') {
    header = navHeader({
      left: (
        <button type="button" className="tv-pin-text-btn" disabled={quickFormLoading} onClick={closeQuickForm}>
          Отмена
        </button>
      ),
      title: quickFormMode === 'edit' ? 'Изменение задачи' : 'Новая задача',
    });
    body = <div key="body-form" className="tv-pin-scroll">{quickTaskForm}</div>;
    footer = (
      <footer key="foot-form" className="tv-pin-footer">
        {quickFormError && <p className="tv-pin-error" role="alert">{quickFormError}</p>}
        <button
          type="submit"
          form={quickFormId}
          className="tv-pin-btn is-primary"
          disabled={quickFormLoading || isTaskRecipientsLoading}
        >
          {quickFormLoading && <LoaderCircle size={14} strokeWidth={2} className="animate-spin" />}
          {quickFormLoading ? 'Сохраняю…' : (quickFormMode === 'edit' ? 'Сохранить' : 'Создать задачу')}
        </button>
      </footer>
    );
  } else if (view === 'palette') {
    header = navHeader({ title: 'Оформление' });
    body = (
      <div key="body-palette" className="tv-pin-scroll">
        <div className="tv-pin-appearance" role="radiogroup" aria-label="Оформление закрепленной задачи">
          {PIN_PALETTES.map((palette) => {
            const active = palette.id === activePalette.id;
            return (
              <button
                key={palette.id}
                type="button"
                role="radio"
                aria-checked={active}
                className={`tv-pin-look ${active ? 'is-active' : ''}`}
                onClick={() => choosePalette(palette.id)}
              >
                <span className="tv-pin-look-frame">
                  <span className="tv-pin-look-preview" style={palette.vars} aria-hidden="true">
                    <span className="tv-pin-look-bar" />
                    <span className="tv-pin-look-body">
                      <span className="tv-pin-look-line" />
                      <span className="tv-pin-look-line is-short" />
                      <span className="tv-pin-look-card" />
                      <span className="tv-pin-look-btn" />
                    </span>
                  </span>
                </span>
                <span className="tv-pin-look-name">
                  <span className="tv-pin-look-radio" aria-hidden="true">
                    <Check size={12} strokeWidth={3} />
                  </span>
                  {palette.label}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    );
  } else if (view === 'list') {
    header = navHeader({
      title: 'Мои задачи',
      right: onCreateTask ? (
        <button
          type="button"
          className="tv-pin-text-btn"
          disabled={!!actionLoadingKey || quickFormLoading}
          onClick={() => {
            returnFocusRef.current = 'list';
            openQuickCreate();
          }}
        >
          <Plus size={15} strokeWidth={2.2} />
          Новая
        </button>
      ) : null,
    });
    body = (
      <div key="body-list" className="tv-pin-scroll">
        <div className="tv-pin-segmented" role="tablist" aria-label="Мои задачи">
          <button
            type="button"
            role="tab"
            aria-selected={taskMenuScope === 'incoming'}
            className={taskMenuScope === 'incoming' ? 'is-active' : ''}
            onClick={() => setTaskMenuScope('incoming')}
          >
            Входящие
            <span className="tv-pin-count">{incomingTaskOptions.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={taskMenuScope === 'outgoing'}
            className={taskMenuScope === 'outgoing' ? 'is-active' : ''}
            onClick={() => setTaskMenuScope('outgoing')}
          >
            Исходящие
            <span className="tv-pin-count">{outgoingTaskOptions.length}</span>
          </button>
        </div>
        {taskMenuGroups.length > 0 ? (
          <>
            {/* Люди — капсулами в одну прокручиваемую строку: список задач под ними
                получает всю ширину панели. Полное имя — в подсказке и в заголовке группы. */}
            <div className="tv-pin-people" ref={peopleStripRef}>
              {taskMenuGroups.map((group) => {
                const open = group.active + group.notAccepted;
                return (
                  <button
                    key={group.key}
                    type="button"
                    className={`tv-pin-person ${activeTaskMenuGroup?.key === group.key ? 'is-active' : ''}`}
                    title={group.name}
                    aria-pressed={activeTaskMenuGroup?.key === group.key}
                    onClick={() => setSelectedMenuPersonKey(group.key)}
                  >
                    <AvatarCircle className="tv-pin-person-avatar" name={group.name} avatarUrl={group.avatarUrl} />
                    <span className="tv-pin-person-name">{pinPersonShortName(group.name)}</span>
                    {open > 0 && (
                      <span className={`tv-pin-person-badge ${group.notAccepted > 0 ? 'is-alert' : ''}`}>
                        {open > 99 ? '99+' : open}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            {activeTaskMenuGroup && (
              <section className="tv-pin-section">
                <div className="tv-pin-section-head">
                  <span>{activeTaskMenuGroup.name}</span>
                  <small>
                    {[
                      activeTaskMenuGroup.notAccepted > 0 ? `ждут принятия: ${activeTaskMenuGroup.notAccepted}` : '',
                      activeTaskMenuGroup.active > 0 ? `в работе: ${activeTaskMenuGroup.active}` : '',
                      activeTaskMenuGroup.done > 0 ? `выполнено: ${activeTaskMenuGroup.done}` : '',
                    ].filter(Boolean).join(' · ')}
                  </small>
                </div>
                <div className="tv-pin-group">
                  {activeTaskMenuGroup.tasks.map((item) => {
                    const itemStatus = STATUS_META[item.status] || { label: item.status || '—', dot: '' };
                    const current = Number(item?.id || 0) === Number(task.id);
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className={`tv-pin-list-row ${current ? 'is-current' : ''}`}
                        aria-current={current ? 'true' : undefined}
                        onClick={() => {
                          onSelectTask?.(item);
                          setTaskMenuOpen(false);
                        }}
                      >
                        <span className="tv-pin-dot" style={itemStatus.dot ? { background: itemStatus.dot } : undefined} aria-hidden="true" />
                        <span className="tv-pin-list-main">
                          <span className="tv-pin-list-title">{item.subject || `Задача #${item.id}`}</span>
                          <span className="tv-pin-list-meta">{itemStatus.label} · {fmt(item.created_at)}</span>
                        </span>
                        {current && <Check className="tv-pin-list-check" size={16} strokeWidth={2.4} aria-hidden="true" />}
                      </button>
                    );
                  })}
                </div>
              </section>
            )}
          </>
        ) : (
          <p className="tv-pin-empty">
            {isTasksLoading
              ? 'Загружаю задачи…'
              : (taskMenuScope === 'outgoing' ? 'Исходящих задач пока нет.' : 'Входящих задач пока нет.')}
          </p>
        )}
      </div>
    );
  } else {
    header = (
      <header key="head-task" className="tv-pin-header" {...dragProps}>
        {!pipWindow && (
          <span className="tv-pin-grip" aria-hidden="true">
            <GripVertical size={14} strokeWidth={2} />
          </span>
        )}
        <div className="tv-pin-heading">
          <span className="tv-pin-heading-title">Закрепленная задача</span>
        </div>
        <div className="tv-pin-tools">
          <button
            type="button"
            className="tv-pin-tool"
            title="Мои задачи"
            aria-label="Мои задачи"
            data-pin-tool="list"
            onClick={() => showView('list')}
          >
            <ListTodo size={16} strokeWidth={1.9} />
          </button>
          <button
            type="button"
            className="tv-pin-tool"
            title="Заметки"
            aria-label="Заметки"
            data-pin-tool="notes"
            onClick={() => showView('notes')}
          >
            <StickyNote size={16} strokeWidth={1.9} />
          </button>
          <button
            type="button"
            className="tv-pin-tool"
            title={expanded ? 'Свернуть' : 'Развернуть'}
            aria-label={expanded ? 'Свернуть' : 'Развернуть'}
            aria-expanded={expanded}
            onClick={toggleExpanded}
          >
            {expanded ? <Minimize2 size={15} strokeWidth={2} /> : <Maximize2 size={15} strokeWidth={2} />}
          </button>
          <button
            ref={moreButtonRef}
            type="button"
            className={`tv-pin-tool ${moreOpen ? 'is-active' : ''}`}
            title="Ещё"
            aria-label="Ещё"
            aria-haspopup="menu"
            aria-expanded={moreOpen}
            data-pin-tool="more"
            onClick={toggleMore}
          >
            <MoreHorizontal size={17} strokeWidth={2} />
          </button>
        </div>
      </header>
    );
    body = (
      <div key="body-task" className={`tv-pin-scroll ${expanded ? '' : 'is-compact'}`}>
        <div className="tv-pin-hero">
          {taskKey && <span className="tv-pin-key">{taskKey}</span>}
          <button
            type="button"
            className="tv-pin-title-btn"
            title="Открыть карточку задачи"
            onClick={() => onOpenDetails?.(task)}
          >
            <span className="tv-pin-title">{task.subject || 'Без темы'}</span>
          </button>
          <div className="tv-pin-tags">
            <span className={`tv-pin-tag is-${statusTone}`}>{sm.label}</span>
            {task?.tag && <span className={`tv-pin-tag is-${tagTone}`}>{tm.label}</span>}
            {/* «Обычная» срочность — не признак, а её отсутствие: как и в списке
                задач, капсулу ставим только срочной и критичной. */}
            {priorityTone && <span className={`tv-pin-tag is-${priorityTone}`}>{pm.label}</span>}
            {task?.is_regulation && <span className="tv-pin-tag is-teal">Регламент</span>}
            {/* Закреплённая задача — та, над которой работают: если работа
                стоит из-за незакрытого вопроса, это надо видеть сразу. */}
            {task?.info_request && <span className="tv-clar-waiting">Ждёт ответа</span>}
          </div>
        </div>

        {expanded && (
          <>
            {String(task.description || '').trim() && (
              <div className="tv-pin-group">
                <p className="tv-pin-description">{task.description}</p>
              </div>
            )}
            <div className="tv-pin-group">
              <div className="tv-pin-row">
                <span className="tv-pin-row-label">{assignees.length > 1 ? 'Исполнители' : 'Исполнитель'}</span>
                <span className="tv-pin-row-value" title={assignees.map((person) => person.name).join(', ')}>
                  {taskAssigneesLabel(task)}
                </span>
              </div>
              <div className="tv-pin-row">
                <span className="tv-pin-row-label">Постановщик</span>
                <span className="tv-pin-row-value">{task?.creator?.name || '—'}</span>
              </div>
              {task?.due_at && (
                <div className="tv-pin-row">
                  <span className="tv-pin-row-label">{overdue ? 'Просрочено' : 'Дедлайн'}</span>
                  <span className={`tv-pin-row-value ${overdue ? 'is-overdue' : ''}`}>{fmtShortDateTime(task.due_at)}</span>
                </div>
              )}
            </div>
            {checklist.length > 0 && (
              <section className="tv-pin-section">
                <div className="tv-pin-section-head">
                  <span>Чек-лист</span>
                  <small>{progress.done} из {progress.total}</small>
                </div>
                <div className="tv-pin-group">
                  {checklist.slice(0, 5).map((item) => {
                    const locked = actionLoadingKey === `${task.id}:checklist:${item.id}` || !!task?.viewer_is_observer;
                    return (
                      <label
                        key={item.id}
                        className={`tv-pin-check ${item.is_done ? 'is-done' : ''} ${locked ? 'is-disabled' : ''}`}
                      >
                        <input
                          type="checkbox"
                          className="tv-pin-check-input"
                          checked={!!item.is_done}
                          disabled={actionLoadingKey === `${task.id}:checklist:${item.id}` || !!task?.viewer_is_observer}
                          onChange={() => onToggleChecklistItem?.(task, item, !item.is_done)}
                        />
                        <span className="tv-pin-check-mark" aria-hidden="true">
                          <Check size={12} strokeWidth={3} />
                        </span>
                        <span className="tv-pin-check-title">{item.title}</span>
                      </label>
                    );
                  })}
                  {checklist.length > 5 && (
                    <div className="tv-pin-row is-note">Ещё пунктов: {checklist.length - 5} — в карточке задачи</div>
                  )}
                </div>
              </section>
            )}
            {attachments.length > 0 && (
              <section className="tv-pin-section">
                <div className="tv-pin-section-head"><span>Файлы задачи</span></div>
                <div className="tv-pin-group">{fileRows(attachments)}</div>
              </section>
            )}
            {compAttachments.length > 0 && (
              <section className="tv-pin-section">
                <div className="tv-pin-section-head"><span>Файлы выполнения</span></div>
                <div className="tv-pin-group">{fileRows(compAttachments)}</div>
              </section>
            )}
          </>
        )}
      </div>
    );
    footer = (
      <footer key="foot-task" className="tv-pin-footer">
        {completeDraft ? (
          <div className="tv-pin-report">
            <textarea
              className="tv-textarea"
              placeholder="Отчёт о проделанной работе"
              autoFocus
              value={completeDraft.body}
              disabled={!!actionLoadingKey}
              onChange={(event) => setCompleteDraft((prev) => ({ ...prev, body: event.target.value }))}
            />
            <input
              className="tv-input"
              placeholder="Затрачено: 3ч, 90"
              value={completeDraft.spent}
              disabled={!!actionLoadingKey}
              onChange={(event) => setCompleteDraft((prev) => ({ ...prev, spent: event.target.value }))}
            />
            <div className="tv-pin-report-actions">
              <button
                type="button"
                className="tv-pin-btn"
                disabled={!!actionLoadingKey}
                onClick={() => setCompleteDraft(null)}
              >
                Отмена
              </button>
              <button
                type="button"
                className="tv-pin-btn is-indigo"
                disabled={!!actionLoadingKey || !completeDraft.body.trim()}
                onClick={async () => {
                  const ok = await onRunAction?.(task, 'completed', {
                    report: completeDraft.body.trim(),
                    spentMinutes: parseSpentInput(completeDraft.spent),
                  });
                  if (ok !== false) setCompleteDraft(null);
                }}
              >
                {actionLoadingKey === `${task.id}:completed` && <LoaderCircle size={14} strokeWidth={2} className="animate-spin" />}
                Сдать работу
              </button>
            </div>
          </div>
        ) : actionButtons.length > 0 ? (
          <div className="tv-pin-actions">
            {actionButtons.map((btn) => {
              const loading = actionLoadingKey === `${task.id}:${btn.action}`;
              const tone = pinToneOf(btn.cls);
              return (
                <button
                  key={btn.action}
                  type="button"
                  className={`tv-pin-btn ${tone ? `is-${tone}` : ''}`}
                  disabled={!!actionLoadingKey}
                  onClick={() => {
                    if (btn.action === 'completed') {
                      setCompleteDraft({ body: '', spent: '' });
                      return;
                    }
                    onRunAction?.(task, btn.action);
                  }}
                >
                  {loading && <LoaderCircle size={14} strokeWidth={2} className="animate-spin" />}
                  {loading ? 'Сохраняю…' : btn.label}
                </button>
              );
            })}
          </div>
        ) : canEditTask && onEditTask ? (
          /* Своя задача, а шагов по статусу нет (ждёт исполнителя): вместо фразы
             «быстрых действий нет» над пустым местом — «Изменить», как была
             видимая кнопка у прежней панели. Удаление — в «⋯». */
          <div className="tv-pin-actions">
            <button
              type="button"
              className="tv-pin-btn"
              disabled={!!actionLoadingKey || quickFormLoading}
              onClick={() => {
                returnFocusRef.current = 'more';
                openQuickEdit();
              }}
            >
              <Pencil size={14} strokeWidth={2} />
              Изменить
            </button>
          </div>
        ) : (
          /* Наблюдателю — почему кнопок нет; в остальных случаях подвал пуст
             и не выводится вовсе (см. ниже): фраза без действия — шум. */
          <p className="tv-pin-hint">
            {task?.viewer_is_observer ? 'Задача открыта вам только на просмотр.' : ''}
          </p>
        )}
      </footer>
    );
    // Ни действий, ни отчёта, ни пояснения наблюдателю — подвал не нужен.
    if (!completeDraft && !actionButtons.length && !(canEditTask && onEditTask) && !task?.viewer_is_observer) {
      footer = null;
    }
  }

  const widgetMarkup = (
    <section
      ref={widgetRef}
      className={`tv-pin-widget ${expanded ? 'is-expanded' : ''} ${isDragging ? 'is-dragging' : ''} ${pipWindow ? 'is-detached' : ''}`}
      style={{
        ...activePalette.vars,
        ...(pipWindow ? {} : (position ? { left: position.x, top: position.y } : { right: 18, bottom: 18 }))
      }}
      aria-label="Закрепленная задача"
    >
      <div className="tv-pin-frame">
        {header}
        {body}
        {footer}
      </div>
      {moreOpen && view === 'task' && (
        <div
          ref={moreMenuRef}
          className={`tv-pin-menu ${moreUp ? 'is-up' : ''}`}
          role="menu"
          aria-label="Действия с закрепленной задачей"
        >
          <button type="button" role="menuitem" className="tv-pin-menu-item" onClick={() => runMenuAction(() => onOpenDetails?.(task))}>
            <PanelRightOpen size={15} strokeWidth={2} />
            Открыть карточку
          </button>
          {canEditTask && onEditTask && (
            <button
              type="button"
              role="menuitem"
              className="tv-pin-menu-item"
              disabled={!!actionLoadingKey || quickFormLoading}
              onClick={() => runMenuAction(() => {
                returnFocusRef.current = 'more';
                openQuickEdit();
              })}
            >
              <Pencil size={15} strokeWidth={2} />
              Изменить
            </button>
          )}
          <button type="button" role="menuitem" className="tv-pin-menu-item" onClick={() => runMenuAction(() => showView('palette', 'more'))}>
            <Palette size={15} strokeWidth={2} />
            Оформление
          </button>
          {!pipWindow && canUseDocumentPictureInPicture && (
            <button type="button" role="menuitem" className="tv-pin-menu-item" onClick={() => runMenuAction(openDocumentPictureInPicture)}>
              <PictureInPicture2 size={15} strokeWidth={2} />
              Открыть поверх окон
            </button>
          )}
          <div className="tv-pin-menu-sep" role="separator" />
          <button type="button" role="menuitem" className="tv-pin-menu-item" onClick={() => runMenuAction(() => onUnpin?.())}>
            <PinOff size={15} strokeWidth={2} />
            Открепить
          </button>
          {canDeleteTask && onDeleteTask && (
            <button
              type="button"
              role="menuitem"
              className="tv-pin-menu-item is-danger"
              disabled={!!actionLoadingKey || quickFormLoading}
              onClick={() => runMenuAction(handleQuickDelete)}
            >
              <Trash2 size={15} strokeWidth={2} />
              Удалить задачу
            </button>
          )}
        </div>
      )}
    </section>
  );

  if (pipContainer) return createPortal(widgetMarkup, pipContainer);
  if (canUseDocumentPictureInPicture) return null;
  return (
    <>
      {widgetMarkup}
      <FilePreviewSheet
        files={fileView ? fileView.items : []}
        index={fileView ? fileView.index : -1}
        onIndexChange={(index) => setFileView((prev) => (prev ? { ...prev, index } : prev))}
        onClose={() => setFileView(null)}
        loadFile={loadPinnedFile}
        cacheKey={taskFileKey}
      />
    </>
  );
});

/* ─── Уведомления: задачи, которые ждут действия пользователя ─── */

const inboxCounterpartLabel = (need) => {
  if (need?.kind === 'review') {
    /* Сдал — конкретный человек, а не «состав»: при нескольких исполнителях
       сдаёт кто-то один, и его имя приходит в completed_by_name. Состав в
       запасе — на старых задачах, где отметки о сдающем нет. */
    const name = need?.task?.completed_by_name || taskAssigneesLabel(need?.task, '');
    return name ? `Сдал: ${name}` : 'Ждёт приёмки';
  }
  if (need?.kind === 'info') {
    const name = need?.task?.info_request?.author_name || taskAssigneesLabel(need?.task, '');
    return name ? `Спросил: ${name}` : 'Просят информацию';
  }
  const name = need?.task?.creator?.name;
  return name ? `Поручил: ${name}` : '';
};

const TaskInbox = ({ needs, unseenCount, onOpen, onMarkAllSeen, compact = false }) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const count = needs.length;
  const groups = useMemo(() => groupTaskActionNeeds(needs), [needs]);
  const close = useCallback(() => setOpen(false), []);
  /* На телефоне список — лист снизу, и системное «назад» закрывает его, а не
     раздел под ним. */
  useScreenBackGesture(compact && open, close);

  useEffect(() => {
    if (!open) return undefined;
    const onDocMouseDown = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDocMouseDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Уведомления исчезают по мере закрытия задач — пустую панель не держим открытой.
  useEffect(() => {
    if (!count) setOpen(false);
  }, [count]);

  return (
    <span className="tv-inbox" ref={rootRef}>
      <button
        type="button"
        className={`${compact ? 'tv-round-btn' : 'tv-btn tv-btn-ghost'} tv-inbox-btn ${unseenCount > 0 ? 'is-hot' : ''}`}
        aria-label={compact ? 'Ждут вас' : undefined}
        onClick={() => setOpen((prev) => !prev)}
        title={unseenCount > 0
          ? `Новых задач, ждущих вашего действия: ${unseenCount}`
          : (count > 0 ? `Все ${count} просмотрены, но ещё ждут действия` : 'Задач, ждущих вашего действия, нет')}
        aria-haspopup="true"
        aria-expanded={open ? 'true' : 'false'}
      >
        {/* На телефоне — значок входящих: у портала уже есть свой колокол, и два
            колокола на одном экране читались бы как одно и то же. */}
        {compact ? <Inbox size={19} strokeWidth={1.9} /> : <><Bell size={13} strokeWidth={2} />Ждут вас</>}
        {unseenCount > 0 && <span className="tv-inbox-count">{unseenCount > 99 ? '99+' : unseenCount}</span>}
      </button>

      {open && compact && (
        /* fixed inset-0 — метка открытого окна для оболочки: пока лист на
           экране, бар разделов и колокол уходят (см. mobile-shell.css). */
        <div className="tv-inbox-dim fixed inset-0" onClick={close} aria-hidden="true" />
      )}
      {open && (
        <div className={`tv-inbox-pop ${compact ? 'is-sheet' : ''}`} role="dialog" aria-label="Задачи, которые ждут вас">
          <div className="tv-inbox-head">
            <span className="tv-inbox-title">Ждут вашего действия</span>
            <span className="tv-inbox-sub">
              {count > 0
                ? `${count} ${pluralRu(count, 'задача', 'задачи', 'задач')}${unseenCount > 0 ? ` · ${unseenCount} ${pluralRu(unseenCount, 'новая', 'новые', 'новых')}` : ''}`
                : 'пусто'}
            </span>
            {compact && (
              <button type="button" className="tv-inbox-done" onClick={close}>Готово</button>
            )}
          </div>

          {count === 0 ? (
            <div className="tv-inbox-empty">
              <span className="tv-inbox-empty-title">Всё разобрано</span>
              <span className="tv-inbox-empty-sub">Ни одна задача не ждёт вашего шага.</span>
            </div>
          ) : (
            <>
              <div className="tv-inbox-scroll">
                {groups.map((group) => {
                  const meta = ACTION_NEED_META[group.kind];
                  return (
                    <div key={group.kind}>
                      <div className="tv-inbox-group-label">
                        <span className="tv-inbox-dot" style={{ background: meta.dot, marginTop: 0 }} />
                        {meta.title} · {group.items.length}
                      </div>
                      {group.items.map((need) => (
                        <button
                          key={need.task.id}
                          type="button"
                          className={`tv-inbox-item ${need.seen ? 'is-seen' : ''}`}
                          title={need.seen ? 'Уже просмотрено — в счётчик не идёт' : undefined}
                          onClick={() => { setOpen(false); onOpen(need); }}
                        >
                          <span className="tv-inbox-dot" style={{ background: meta.dot }} />
                          <span className="tv-inbox-item-main">
                            <span className="tv-inbox-item-subject">{need.task?.subject || 'Без темы'}</span>
                            <span className="tv-inbox-item-meta">
                              <span>{inboxCounterpartLabel(need) || meta.hint}</span>
                              {need.dueAt && (
                                <span className={need.kind === 'overdue' ? 'tv-inbox-item-due' : ''}>
                                  · {need.kind === 'overdue' ? 'просрочено с' : 'до'} {fmtShortDateTime(need.task.due_at)}
                                </span>
                              )}
                            </span>
                          </span>
                          <ChevronRight />
                        </button>
                      ))}
                    </div>
                  );
                })}
              </div>
              {unseenCount > 0 && (
                <div className="tv-inbox-foot">
                  <button type="button" className="tv-inbox-mark-all" onClick={() => onMarkAllSeen(needs)}>
                    Отметить все просмотренными
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </span>
  );
};

/* ─── Skeleton loading ─── */
const SkeletonList = ({ count = 4 }) => (
  <div className="tv-task-list">
    {Array.from({ length: count }).map((_, i) => (
      <div key={i} className="tv-skeleton tv-skeleton-row" style={{ opacity: 1 - i * 0.15 }} />
    ))}
  </div>
);

/* ─── Main Component ─── */
const TasksView = ({
  user,
  showToast,
  apiBaseUrl,
  withAccessTokenHeader,
  pinnedTaskId = null,
  onPinTask,
  onUnpinTask,
  onPinnedTaskSync,
  focusTaskRequest,
  externalRefreshToken = 0,
  onActionNeedsChange,
  onOpenProduction = null,
}) => {
  const currentUserRole = normalizeRole(user?.role);
  /* Гейт раздела. Кроме управленческих ролей сюда попадает рядовой, которому
     раздел выдан: бэк-офису — картой отдела, IT — своим набором. Спрашиваем
     отдел, а не список ролей: у оператора IT и у оператора линии роль одна и та
     же строка, и различает их только отдел. Правило то же, что у пункта меню, —
     rankAndFileTasksAllowed (departmentViews.js).
     От этого флага зависит не только рендер, но и ВСЕ загрузки раздела ниже:
     оставив его узким, получим открытый экран, в который не приехали данные. */
  const canAccessTasks = isAdminLikeRole(currentUserRole) || isSupervisorRole(currentUserRole) || currentUserRole === 'trainer'
    || rankAndFileTasksAllowed(user);
  const [tasks,               setTasks]               = useState([]);
  /* Список «моих задач» хотя бы раз загрузился. До этого счётчик уведомлений
     наружу не репортится: на маунте tasks=[] даёт транзиентный ноль, который
     App принял бы за «всё разгребли» и погасил бы источник в колоколе. */
  const [hasLoadedTasks,      setHasLoadedTasks]      = useState(false);
  const [pagedTasks,          setPagedTasks]          = useState([]);
  const [recipients,          setRecipients]          = useState([]);
  const [boardPeople,         setBoardPeople]         = useState([]);
  /* Доска проекта раздела (задача #401) — см. taskProjects.js.
     null означает «ещё не знаем»: до ответа сервера ни один список не грузим,
     иначе первый экран показал бы все проекты и тут же перерисовался на свой. */
  const [projects,            setProjects]            = useState([]);
  const [projectsMeta,        setProjectsMeta]        = useState({
    unassignedCount: 0, canManage: false, defaultProjectId: null, departmentProjectId: null, candidates: [],
  });
  const [projectFilter,       setProjectFilter]       = useState(null);
  // Раздел открытой доски. При смене доски — снова «Все разделы».
  const [sectionFilter,       setSectionFilter]       = useState(TASK_SECTION_ALL);
  const [projectsSheetOpen,   setProjectsSheetOpen]   = useState(false);
  const [isTasksLoading,      setIsTasksLoading]      = useState(false);
  // Стартуем в «гружусь»: до ответа сервера пустой список читался бы как «задач нет».
  const [isPagedTasksLoading, setIsPagedTasksLoading] = useState(true);
  const [isRecipientsLoading, setIsRecipientsLoading] = useState(false);
  const [isCreateLoading,     setIsCreateLoading]     = useState(false);
  const [actionLoadingKey,    setActionLoadingKey]    = useState('');
  const [selectedFiles,       setSelectedFiles]       = useState([]);
  const [searchQuery,         setSearchQuery]         = useState('');
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [filterStatus,        setFilterStatus]        = useState('');
  const [filterTag,           setFilterTag]           = useState('');
  const [filterPriority,      setFilterPriority]      = useState('');
  const [allTasksPage,        setAllTasksPage]        = useState(1);
  const [allTasksTotal,       setAllTasksTotal]       = useState(0);
  const [filteredTasksTotal,  setFilteredTasksTotal]  = useState(0);
  const [personTasks,         setPersonTasks]         = useState([]);
  const [personTasksPage,     setPersonTasksPage]     = useState(1);
  const [personTasksTotal,    setPersonTasksTotal]    = useState(0);
  const [isPersonTasksLoading, setIsPersonTasksLoading] = useState(false);

  const [createOpen,        setCreateOpen]        = useState(false);
  const [editModal,         setEditModal]         = useState({ open: false, taskId: null, taskSubject: '' });
  const [deleteModal,       setDeleteModal]       = useState({ open: false, taskId: null, taskSubject: '' });
  const [editForm,          setEditForm]          = useState(() => buildEmptyTaskForm());
  const [drawerTask,        setDrawerTask]        = useState(null);
  /* «Мы на телефоне»: от него зависят и адрес открытой задачи (эффект ниже), и окна. */
  const isMobileShell = useIsMobileShell();
  const [completeModal,     setCompleteModal]     = useState({ open: false, taskId: null, taskSubject: '', estimateMinutes: null });
  const [statusModal,       setStatusModal]       = useState({ open: false, taskId: null, action: '', taskSubject: '' });
  const [completionSummary, setCompletionSummary] = useState('');
  const [completionSpent,   setCompletionSpent]   = useState('');
  const [completionFiles,   setCompletionFiles]   = useState([]);
  const [statusComment,     setStatusComment]     = useState('');
  const [statusFiles,       setStatusFiles]       = useState([]);
  const [myTasksTab,        setMyTasksTab]        = useState('incoming');
  const [incomingPersonKey, setIncomingPersonKey] = useState(null);
  const [outgoingPersonKey, setOutgoingPersonKey] = useState(null);

  const fileInputRef           = useRef(null);
  const searchRef              = useRef(null);
  const pagedRequestIdRef      = useRef(0);
  const personTasksRequestIdRef = useRef(0);
  // Ссылка отработана — карточку открыли; запрос ушёл — второй раз не идём.
  const handledFocusRequestRef = useRef(0);
  const fetchedFocusRequestRef = useRef(0);
  const hasSyncedDrawerUrlRef   = useRef(false);
  const [form, setForm] = useState(() => buildEmptyTaskForm());
  /* Бриф продакшна: пока «Поставить задачу» не нажимали, ничего не краснеет —
     пустая форма не ошибка. После попытки поля с пунктами обведены красным,
     а заполнили — рамка снимается сразу (пункты считаются заново). */
  const [createIssuesShown, setCreateIssuesShown] = useState(false);
  const [editIssuesShown, setEditIssuesShown] = useState(false);
  const createBodyRef = useRef(null);
  const editBodyRef = useRef(null);
  const [notesOpen, setNotesOpen] = useState(false);
  // Карточка, к которой ведёт клик по уведомлению: доска подсветит её и подскроллит.
  const [boardFocus, setBoardFocus] = useState(null);
  // Дедлайн истекает без перезагрузки страницы — пересчитываем «просрочено» раз в минуту.
  const [actionNeedsNow, setActionNeedsNow] = useState(() => Date.now());
  // Просмотренные уведомления до ответа сервера: ключ задача+причина+updated_at.
  const [actionSeenLocal, setActionSeenLocal] = useState(() => new Set());
  // Серверная сводка по всем видимым задачам — цифры «Обзора» и бейдж бэклога.
  const [tasksSummary, setTasksSummary] = useState(null);
  // Локально поправленные задачи (чек-лист, отчёты) — доска накладывает их на свою страницу.
  const [taskPatches, setTaskPatches] = useState({});
  // Доска перезагружает страницу, когда данные раздела изменились.
  const [boardReloadToken, setBoardReloadToken] = useState(0);
  const [workspaceTab, setWorkspaceTab] = useState(() => {
    if (typeof window === 'undefined') return 'overview';
    const stored = window.localStorage.getItem(WORKSPACE_TAB_STORAGE_KEY);
    return WORKSPACE_TABS.some((tab) => tab.id === stored) ? stored : 'overview';
  });

  const selectWorkspaceTab = useCallback((tabId) => {
    setWorkspaceTab(tabId);
    try { window.localStorage.setItem(WORKSPACE_TAB_STORAGE_KEY, tabId); } catch (error) { /* private mode */ }
  }, []);

  const isProjectResolved = projectFilter !== null;
  /* Обновление списка проектов не должно зависеть от выбранной доски: попади она
     в зависимости fetchProjects — смена доски перезапускала бы загрузку раздела.
     Поэтому текущий выбор читаем через ref. */
  const projectFilterRef = useRef(null);
  useEffect(() => { projectFilterRef.current = projectFilter; }, [projectFilter]);
  const projectOptions = useMemo(
    () => buildProjectOptions(projects, projectsMeta.unassignedCount, projectFilter ?? TASK_PROJECT_ALL),
    [projects, projectsMeta.unassignedCount, projectFilter]
  );
  // Переключатель — только когда есть хоть одна доска: иначе это кнопка с одним «Все проекты».
  const showProjectSwitcher = hasProjectBoards(projects) || (projectFilter ?? TASK_PROJECT_ALL) !== TASK_PROJECT_ALL;
  /* Фильтр разделов — у открытой доски, где разделы заведены. Раздел, которого
     уже нет (удалили), сам возвращается на «Все разделы». */
  const currentProject = useMemo(() => findProject(projects, projectFilter), [projects, projectFilter]);
  const effectiveSectionFilter = currentProject ? resolveSectionFilter(currentProject, sectionFilter) : TASK_SECTION_ALL;
  const sectionOptions = useMemo(
    () => buildSectionOptions(currentProject, effectiveSectionFilter),
    [currentProject, effectiveSectionFilter]
  );
  const showSectionSwitcher = sectionsOf(currentProject).length > 0;

  const changeProjectFilter = useCallback((next) => {
    const value = String(next || TASK_PROJECT_ALL);
    setProjectFilter((prev) => (prev === value ? prev : value));
    setSectionFilter(TASK_SECTION_ALL);
    setAllTasksPage(1);
    writeStoredValue(projectStorageKey(user?.id), value);
  }, [user?.id]);

  const changeSectionFilter = useCallback((next) => {
    setSectionFilter(String(next || TASK_SECTION_ALL));
    setAllTasksPage(1);
  }, []);

  /* Проект, который форма подставит сама: открытая доска → последний, куда
     ставили → доска своего отдела. Доску отдела форма берёт и у рядового
     бэк-офиса, хотя переключатель ему её не выбирает: там сужение прятало бы
     чужие поручения, а здесь это просто подсказка, куда поставить. */
  const composerDefaultProject = useMemo(() => resolveComposerProject(projects, {
    boardValue: projectFilter,
    lastUsed: readStoredValue(composerProjectStorageKey(user?.id)),
    defaultProjectId: projectsMeta.departmentProjectId,
  }), [projects, projectFilter, projectsMeta.departmentProjectId, user?.id]);
  // Открыт раздел этой же доски — новая задача встанет в него.
  const composerDefaultSection = (
    composerDefaultProject && composerDefaultProject === String(projectFilter ?? '')
    && Number(effectiveSectionFilter) > 0
  ) ? String(effectiveSectionFilter) : '';

  useEffect(() => {
    const timer = setInterval(() => setActionNeedsNow(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);

  const showToastRef = useRef(showToast);
  useEffect(() => { showToastRef.current = showToast; }, [showToast]);

  const notify = useCallback((msg, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(msg, type);
  }, []);

  const copyTaskLink = useCallback(async (task) => {
    const taskLink = buildTaskDeepLink(task);
    if (!taskLink) {
      notify('Не удалось собрать ссылку на задачу', 'error');
      return;
    }
    try {
      await navigator.clipboard.writeText(taskLink);
      notify('Ссылка на задачу скопирована');
    } catch (error) {
      notify('Не удалось скопировать ссылку на задачу', 'error');
    }
  }, [notify]);

  const buildHeaders = useCallback((extra = {}) => {
    const h = { ...(extra || {}) };
    if (user?.id)     h['X-User-Id'] = String(user.id);
    return typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(h) : h;
  }, [user?.id, withAccessTokenHeader]);
  const notesState = useTaskNotes({
    userId: user?.id,
    apiBaseUrl,
    buildHeaders,
    notify,
  });

  /* Сотрудники для переключателя досок: сервер отдаёт только тех, у кого есть
     задачи, без уволенных и с отделом для группировки. */
  const fetchBoardPeople = useCallback(async () => {
    try {
      const res = await axios.get(`${apiBaseUrl}/api/tasks/board_people`, { headers: buildHeaders() });
      setBoardPeople(Array.isArray(res?.data?.people) ? res.data.people : []);
    } catch (e) {
      setBoardPeople([]);
    }
  }, [apiBaseUrl, buildHeaders]);

  /* Проекты: доски, к которым у человека есть доступ, и те, где лежат видимые
     ему задачи. Сервер подсказывает доску по умолчанию — ту, что привязана к
     отделу человека (у главы — к возглавляемому), если такая одна.
     Не удалось загрузить — остаёмся на «Все проекты»: сузить выборку по доске,
     списка которых нет, значит показать пустой раздел без объяснения. */
  const fetchProjects = useCallback(async () => {
    let list = [];
    let meta = {
      unassignedCount: 0, canManage: false, defaultProjectId: null, departmentProjectId: null, candidates: [],
    };
    try {
      const res = await axios.get(`${apiBaseUrl}/api/tasks/projects`, { headers: buildHeaders() });
      list = Array.isArray(res?.data?.projects) ? res.data.projects : [];
      meta = {
        unassignedCount: Number(res?.data?.unassigned_count || 0),
        canManage: Boolean(res?.data?.can_manage),
        defaultProjectId: res?.data?.default_project_id ?? null,
        departmentProjectId: res?.data?.department_project_id ?? null,
        candidates: Array.isArray(res?.data?.candidates) ? res.data.candidates : [],
      };
    } catch (e) {
      list = [];
    }
    setProjects((prev) => mergeSelectedProject(list, prev, projectFilterRef.current));
    setProjectsMeta(meta);
    setProjectFilter((prev) => (prev !== null
      ? prev
      : resolveInitialProject(
        list, meta.defaultProjectId, readStoredValue(projectStorageKey(user?.id)), meta.unassignedCount
      )));
  }, [apiBaseUrl, buildHeaders, user?.id]);

  const fetchRecipients = useCallback(async () => {
    setIsRecipientsLoading(true);
    try {
      const res = await axios.get(`${apiBaseUrl}/api/tasks/recipients`, { headers: buildHeaders() });
      setRecipients(Array.isArray(res?.data?.recipients) ? res.data.recipients : []);
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось загрузить список сотрудников', 'error');
    } finally { setIsRecipientsLoading(false); }
  }, [apiBaseUrl, buildHeaders, notify]);

  /* «Мои задачи»: всё, где пользователь участник. Этого набора хватает группам
     по сотрудникам, уведомлениям и закреплённой задаче — целиком базу раздел
     больше не тянет, доска грузит свою страницу отдельным запросом. */
  const fetchTasks = useCallback(async () => {
    setIsTasksLoading(true);
    try {
      const res  = await axios.get(`${apiBaseUrl}/api/tasks`, {
        headers: buildHeaders(),
        params: { only_my: 1 }
      });
      const list = Array.isArray(res?.data?.tasks) ? res.data.tasks : [];
      setTasks(list);
      setHasLoadedTasks(true);
      setDrawerTask(prev => prev ? (list.find(t => t.id === prev.id) ?? prev) : null);
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось загрузить задачи', 'error');
    } finally { setIsTasksLoading(false); }
  }, [apiBaseUrl, buildHeaders, notify]);

  /* Загрузчик страницы доски: доска сотрудника — только его задачи, общая —
     страница по количеству. Сводка нужна шапке доски, чтобы её счётчики
     считались по всей доске, а не по видимой странице. */
  const loadBoardTasks = useCallback(async ({ scope, projectId, sectionId, mode, sort, column, limit, offset, withSummary }) => {
    try {
      const res = await axios.get(`${apiBaseUrl}/api/tasks`, {
        headers: buildHeaders(),
        params: boardQueryParams({ scope, projectId, sectionId, mode, sort, column, limit, offset, withSummary })
      });
      const data = res?.data || {};
      return {
        tasks: Array.isArray(data.tasks) ? data.tasks : [],
        total: Number(data?.totals?.filtered || 0),
        summary: data?.summary || null,
      };
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось загрузить доску', 'error');
      return { tasks: [], total: 0, summary: null };
    }
  }, [apiBaseUrl, buildHeaders, notify]);

  /* Выгрузка идёт blob'ом через axios: простая ссылка <a href> не несёт токен и
     упирается в 401, потому что авторизация здесь bearer, а не кука. */
  const exportBoardTasks = useCallback(async ({ scope, projectId, sectionId }) => {
    try {
      const res = await axios.get(`${apiBaseUrl}/api/tasks/export`, {
        headers: buildHeaders(),
        params: scopeQueryParams(scope, projectId, sectionId),
        responseType: 'blob',
      });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      const today = new Date();
      const stamp = [
        today.getFullYear(),
        String(today.getMonth() + 1).padStart(2, '0'),
        String(today.getDate()).padStart(2, '0'),
      ].join('-');
      link.download = `Задачи_${stamp}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      notify('Не удалось сформировать выгрузку', 'error');
    }
  }, [apiBaseUrl, buildHeaders, notify]);

  useEffect(() => {
    if (!pinnedTaskId || typeof onPinnedTaskSync !== 'function') return;
    const latestPinnedTask = tasks.find((task) => Number(task?.id || 0) === Number(pinnedTaskId));
    if (latestPinnedTask) onPinnedTaskSync(latestPinnedTask);
  }, [tasks, pinnedTaskId, onPinnedTaskSync]);

  useEffect(() => {
    if (!hasSyncedDrawerUrlRef.current) {
      hasSyncedDrawerUrlRef.current = true;
      if (!drawerTask?.id && typeof window !== 'undefined') {
        try {
          const params = new URL(window.location.href).searchParams;
          const currentTaskId = Number(params.get(TASK_ID_QUERY_PARAM) || 0);
          if (currentTaskId > 0 || normalizeTaskKey(params.get(TASK_KEY_QUERY_PARAM))) return;
        } catch (error) {
          // Fall through to the regular sync path.
        }
      }
    }
    /* Телефон: номер задачи в адрес НЕ пишем, только снимаем. replaceState ложится
       на ту запись истории, что окажется ПОД экраном карточки (свою запись стек
       жестов кладёт позже), и после пары жестов «назад» указатель опускался на
       неё: раздел читал task_id как переход по ссылке и заново открывал уже
       закрытую карточку. Адресной строки на телефоне не видно, а ссылку на
       задачу собирает buildTaskDeepLink. */
    syncTaskDeepLink(isMobileShell ? null : (drawerTask?.id ? drawerTask : null));
  }, [drawerTask?.id, drawerTask?.key, isMobileShell]);

  const fetchPagedTasks = useCallback(async () => {
    const requestId = ++pagedRequestIdRef.current;
    setIsPagedTasksLoading(true);
    try {
      const params = {
        limit: TASKS_PAGE_SIZE,
        offset: (allTasksPage - 1) * TASKS_PAGE_SIZE,
        ...projectQueryParams(projectFilter),
        ...sectionQueryParams(effectiveSectionFilter)
      };
      if (debouncedSearchQuery) params.q = debouncedSearchQuery;
      if (filterStatus) params.status = filterStatus;
      if (filterTag) params.tag = filterTag;
      if (filterPriority) params.priority = filterPriority;

      const res = await axios.get(`${apiBaseUrl}/api/tasks`, {
        headers: buildHeaders(),
        params
      });
      if (requestId !== pagedRequestIdRef.current) return;

      const data = res?.data || {};
      const list = Array.isArray(data?.tasks) ? data.tasks : [];
      const totals = data?.totals || {};
      const totalAll = Number(totals?.all);
      const totalFiltered = Number(totals?.filtered);

      setPagedTasks(list);
      setTasksSummary(data?.summary || null);
      setAllTasksTotal(Number.isFinite(totalAll) ? totalAll : list.length);
      setFilteredTasksTotal(Number.isFinite(totalFiltered) ? totalFiltered : list.length);
      setDrawerTask(prev => prev ? (list.find(t => t.id === prev.id) ?? prev) : null);
    } catch (e) {
      if (requestId === pagedRequestIdRef.current) {
        notify(e?.response?.data?.error || 'Не удалось загрузить список задач', 'error');
      }
    } finally {
      if (requestId === pagedRequestIdRef.current) setIsPagedTasksLoading(false);
    }
  }, [
    apiBaseUrl,
    buildHeaders,
    notify,
    allTasksPage,
    debouncedSearchQuery,
    projectFilter,
    effectiveSectionFilter,
    filterStatus,
    filterTag,
    filterPriority,
  ]);

  useEffect(() => {
    if (!user || !canAccessTasks) return;
    fetchRecipients();
    fetchBoardPeople();
    fetchProjects();
    fetchTasks();
  }, [user, canAccessTasks, fetchRecipients, fetchBoardPeople, fetchProjects, fetchTasks]);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery.trim());
    }, 280);
    return () => clearTimeout(t);
  }, [searchQuery]);

  useEffect(() => {
    if (!user || !canAccessTasks || !isProjectResolved) return;
    fetchPagedTasks();
  }, [user, canAccessTasks, isProjectResolved, fetchPagedTasks]);

  // Keyboard: Ctrl/Cmd+K → focus search
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const currentUserId = Number(user?.id || 0);

  /* Уведомления раздела: задачи, ждущие шага именно этого пользователя.
     Считаем по уже загруженному полному списку — отдельный запрос не нужен,
     а бейдж сайдбара живёт на том же правиле (см. taskActionNeeds.js). */
  const actionNeeds = useMemo(
    () => collectTaskActionNeeds(tasks, currentUserId, actionNeedsNow, actionSeenLocal),
    [tasks, currentUserId, actionNeedsNow, actionSeenLocal]
  );
  const actionNeedsUnseen = useMemo(() => countUnseenActionNeeds(actionNeeds), [actionNeeds]);

  /* Причина по одной задаче — для мигающей собачки на карточке доски. */
  const actionNeedOf = useCallback((task) => {
    const need = taskActionNeed(task, currentUserId, actionNeedsNow);
    if (!need) return null;
    return { ...need, seen: isActionNeedSeen(task, need.kind, actionSeenLocal) };
  }, [currentUserId, actionNeedsNow, actionSeenLocal]);

  useEffect(() => {
    // Ноль до первой загрузки (или после её отказа) — не знание, а отсутствие
    // данных: наружу его отдавать нельзя, App погасил бы источник в колоколе.
    if (!hasLoadedTasks) return;
    if (typeof onActionNeedsChange === 'function') onActionNeedsChange(actionNeedsUnseen);
  }, [hasLoadedTasks, actionNeedsUnseen, onActionNeedsChange]);

  /* Открыли уведомление — гасим его в счётчике. Задача из списка не исчезает:
     она всё ещё ждёт действия, просто перестаёт быть новостью. Локальный набор
     ключей даёт мгновенный отклик, сервер помнит отметку между сессиями. */
  const markActionNeedsSeen = useCallback(async (items) => {
    const fresh = (Array.isArray(items) ? items : [items]).filter((need) => need?.task?.id && !need.seen);
    if (!fresh.length) return;
    setActionSeenLocal((prev) => {
      const next = new Set(prev);
      fresh.forEach((need) => next.add(actionNeedSeenKey(need.task, need.kind)));
      return next;
    });
    await Promise.all(fresh.map((need) => axios
      .post(`${apiBaseUrl}/api/tasks/${need.task.id}/action_seen`, { kind: need.kind }, { headers: buildHeaders() })
      .catch(() => null)));
  }, [apiBaseUrl, buildHeaders]);

  // Переход из уведомления: доска → нужная карточка → карточка задачи.
  const openActionNeed = useCallback((need) => {
    const task = need?.task;
    if (!task?.id) return;
    markActionNeedsSeen(need);
    selectWorkspaceTab('board');
    setBoardFocus({ taskId: Number(task.id), token: Date.now() });
    setDrawerTask(task);
  }, [markActionNeedsSeen, selectWorkspaceTab]);

  /* Открыл карточку — значит прочитал: собачка гаснет и счётчик падает,
     каким бы путём задачу ни открыли (уведомление, доска, список). */
  useEffect(() => {
    if (!drawerTask?.id) return;
    const need = actionNeedOf(drawerTask);
    if (need && !need.seen) markActionNeedsSeen({ ...need, task: drawerTask });
  }, [drawerTask, actionNeedOf, markActionNeedsSeen]);

  // «Входящие» — где я среди исполнителей, а не только где я первый в списке.
  const incomingTasks = useMemo(
    () => tasks.filter(t => isTaskAssignee(t, currentUserId)),
    [tasks, currentUserId]
  );
  const outgoingTasks = useMemo(
    () => tasks.filter(t => Number(t?.creator?.id || 0) === currentUserId),
    [tasks, currentUserId]
  );

  const incomingGroups = useMemo(
    () => groupTasksByPerson(incomingTasks, t => t?.creator),
    [incomingTasks]
  );
  const outgoingGroups = useMemo(
    () => groupTasksByPerson(outgoingTasks, t => taskAssignees(t)),
    [outgoingTasks]
  );

  const selectedIncomingGroup = useMemo(
    () => incomingGroups.find(g => g.key === incomingPersonKey) || null,
    [incomingGroups, incomingPersonKey]
  );
  const selectedOutgoingGroup = useMemo(
    () => outgoingGroups.find(g => g.key === outgoingPersonKey) || null,
    [outgoingGroups, outgoingPersonKey]
  );
  const activeSelectedGroup = myTasksTab === 'incoming' ? selectedIncomingGroup : selectedOutgoingGroup;
  const activePersonScope = myTasksTab === 'incoming' ? 'incoming' : 'outgoing';
  const isPersonDrilldown = !!activeSelectedGroup;

  const fetchPersonTasks = useCallback(async () => {
    const personId = Number(activeSelectedGroup?.personId || 0);
    if (!personId) {
      setPersonTasks([]);
      setPersonTasksTotal(0);
      return;
    }

    const requestId = ++personTasksRequestIdRef.current;
    setIsPersonTasksLoading(true);
    try {
      const res = await axios.get(`${apiBaseUrl}/api/tasks`, {
        headers: buildHeaders(),
        params: {
          limit: TASKS_PAGE_SIZE,
          offset: (personTasksPage - 1) * TASKS_PAGE_SIZE,
          person_id: personId,
          person_scope: activePersonScope
        }
      });
      if (requestId !== personTasksRequestIdRef.current) return;

      const data = res?.data || {};
      const list = Array.isArray(data?.tasks) ? data.tasks : [];
      const totalFiltered = Number(data?.totals?.filtered);
      setPersonTasks(list);
      setPersonTasksTotal(Number.isFinite(totalFiltered) ? totalFiltered : list.length);
      setDrawerTask(prev => prev ? (list.find(t => t.id === prev.id) ?? prev) : null);
    } catch (e) {
      if (requestId === personTasksRequestIdRef.current) {
        notify(e?.response?.data?.error || 'Не удалось загрузить задачи сотрудника', 'error');
      }
    } finally {
      if (requestId === personTasksRequestIdRef.current) setIsPersonTasksLoading(false);
    }
  }, [
    activePersonScope,
    activeSelectedGroup?.personId,
    apiBaseUrl,
    buildHeaders,
    notify,
    personTasksPage,
    user?.id
  ]);

  const refreshTasksData = useCallback(async () => {
    // Счётчики проектов живут от задач: поставили задачу — число у доски выросло.
    const jobs = [fetchTasks(), fetchPagedTasks(), fetchProjects()];
    if (isPersonDrilldown) jobs.push(fetchPersonTasks());
    setBoardReloadToken((prev) => prev + 1);
    await Promise.all(jobs);
    setTaskPatches({});
  }, [fetchTasks, fetchPagedTasks, fetchProjects, fetchPersonTasks, isPersonDrilldown]);

  /* board: false — доску не трогаем: она перечитает колонки сама и переставит
     карточку один раз. Общие патчи гаснут, как только пришли списки, а колонки
     доски грузятся отдельно, — и карточка прыгала бы туда-обратно-туда. */
  const patchTaskEverywhere = useCallback((taskId, updater, { board = true } = {}) => {
    const normalizedTaskId = Number(taskId || 0);
    if (!normalizedTaskId || typeof updater !== 'function') return;
    const current = [drawerTask, ...tasks, ...pagedTasks, ...personTasks]
      .filter(Boolean)
      .find((item) => Number(item?.id || 0) === normalizedTaskId) || { id: normalizedTaskId };
    const nextTask = updater(current);
    if (!nextTask) return;
    const replace = (item) => (Number(item?.id || 0) === normalizedTaskId ? nextTask : item);
    setTasks(prev => prev.map(replace));
    setPagedTasks(prev => prev.map(replace));
    setPersonTasks(prev => prev.map(replace));
    setDrawerTask(prev => (Number(prev?.id || 0) === normalizedTaskId ? nextTask : prev));
    if (board) setTaskPatches(prev => ({ ...prev, [normalizedTaskId]: nextTask }));
    if (Number(pinnedTaskId || 0) === normalizedTaskId) onPinnedTaskSync?.(nextTask);
  }, [drawerTask, tasks, pagedTasks, personTasks, pinnedTaskId, onPinnedTaskSync]);

  const toggleChecklistItem = useCallback(async (task, item, isDone) => {
    const taskId = Number(task?.id || 0);
    const itemId = Number(item?.id || 0);
    if (!taskId || !itemId) return;
    const key = `${taskId}:checklist:${itemId}`;
    setActionLoadingKey(key);
    try {
      const res = await axios.patch(
        `${apiBaseUrl}/api/tasks/${taskId}/checklist/${itemId}`,
        { is_done: Boolean(isDone) },
        { headers: buildHeaders() }
      );
      const updatedItem = res?.data?.item || { ...item, is_done: Boolean(isDone) };
      patchTaskEverywhere(taskId, (current) => ({
        ...current,
        checklist: (Array.isArray(current?.checklist) ? current.checklist : [])
          .map((row) => Number(row?.id || 0) === itemId ? { ...row, ...updatedItem } : row)
      }));
      notify(isDone ? 'Пункт чек-листа выполнен' : 'Пункт чек-листа открыт');
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось обновить чек-лист', 'error');
      // Пункта уже нет — чек-лист поменяли, пока задача была открыта: показываем
      // нынешний, иначе следующая галочка снова ушла бы мимо.
      if (e?.response?.status === 404) refreshTasksData();
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, patchTaskEverywhere, refreshTasksData]);

  const saveChecklistNote = useCallback(async (task, item, note) => {
    const taskId = Number(task?.id || 0);
    const itemId = Number(item?.id || 0);
    if (!taskId || !itemId) return;
    const nextNote = String(note || '').trim();
    if (nextNote === String(item?.result_note || '').trim()) return;
    const key = `${taskId}:checklist:${itemId}`;
    setActionLoadingKey(key);
    try {
      const res = await axios.patch(
        `${apiBaseUrl}/api/tasks/${taskId}/checklist/${itemId}`,
        { is_done: true, result_note: nextNote },
        { headers: buildHeaders() }
      );
      const updatedItem = res?.data?.item || { ...item, result_note: nextNote };
      patchTaskEverywhere(taskId, (current) => ({
        ...current,
        checklist: (Array.isArray(current?.checklist) ? current.checklist : [])
          .map((row) => Number(row?.id || 0) === itemId ? { ...row, ...updatedItem } : row)
      }));
      notify('Итог по пункту сохранён');
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось сохранить итог', 'error');
      if (e?.response?.status === 404) refreshTasksData();
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, patchTaskEverywhere, refreshTasksData]);

  useEffect(() => {
    if (!externalRefreshToken || !user || !canAccessTasks) return;
    refreshTasksData();
  }, [externalRefreshToken, user, canAccessTasks, refreshTasksData]);

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil(filteredTasksTotal / TASKS_PAGE_SIZE)),
    [filteredTasksTotal]
  );
  const personTotalPages = useMemo(
    () => Math.max(1, Math.ceil(personTasksTotal / TASKS_PAGE_SIZE)),
    [personTasksTotal]
  );

  useEffect(() => {
    if (allTasksPage > totalPages) setAllTasksPage(totalPages);
  }, [allTasksPage, totalPages]);

  useEffect(() => {
    if (personTasksPage > personTotalPages) setPersonTasksPage(personTotalPages);
  }, [personTasksPage, personTotalPages]);

  useEffect(() => {
    setPersonTasksPage(1);
  }, [activeSelectedGroup?.key, myTasksTab]);

  useEffect(() => {
    if (!user || !canAccessTasks || !isPersonDrilldown) return;
    fetchPersonTasks();
  }, [user, canAccessTasks, isPersonDrilldown, fetchPersonTasks]);

  /* Переход по ссылке на задачу. Эффект перезапускается на каждый ответ раздела
     («мои», страница списка, задачи сотрудника), поэтому «ссылка отработана» —
     это КАРТОЧКА ОТКРЫТА, а не «мы что-то предприняли». Раньше первый же такой
     перезапуск гасил своим cleanup'ом уже улетевший запрос за задачей и при этом
     считал ссылку отработанной — карточка не открывалась ни ответом на запрос,
     ни догрузившимся набором. На «Обзоре» это почти всегда успевало отработать,
     а на «Доске», где к тем же ответам добавляется по запросу на колонку и ответ
     по ссылке приходит последним, задача не открывалась вообще. */
  useEffect(() => {
    const plan = planTaskFocus({
      request: focusTaskRequest,
      handledRequestId: handledFocusRequestRef.current,
      fetchedRequestId: fetchedFocusRequestRef.current,
      pools: [tasks, pagedTasks, personTasks],
    });
    if (plan.kind === 'idle') return;
    if (plan.kind === 'open') {
      handledFocusRequestRef.current = plan.requestId;
      setDrawerTask(plan.task);
      return;
    }
    /* Задачи может не быть в загруженных наборах: раздел держит только «мои»,
       а ссылка (Telegram, закреплённая задача) ведёт на любую доступную.
       summary=0 обязателен: из этого ответа берётся одна задача, а сводка стоит
       семи агрегатов по всей базе — с ней запрос за одной строкой приезжал
       последним из всех запросов раздела. */
    fetchedFocusRequestRef.current = plan.requestId;
    axios.get(`${apiBaseUrl}/api/tasks`, {
      headers: buildHeaders(),
      // По ключу сервер найдёт и задачу, перенесённую из проекта: старый ключ живёт в псевдонимах.
      params: plan.taskId
        ? { task_id: plan.taskId, limit: 1, summary: 0 }
        : { task_key: plan.taskKey, limit: 1, summary: 0 },
    })
      .then((res) => {
        const found = (Array.isArray(res?.data?.tasks) ? res.data.tasks : [])
          .find((task) => (plan.taskId ? Number(task?.id || 0) === plan.taskId : Boolean(task?.id)));
        if (!shouldOpenFetchedTask({
          task: found,
          requestId: plan.requestId,
          handledRequestId: handledFocusRequestRef.current,
          fetchedRequestId: fetchedFocusRequestRef.current,
        })) return;
        handledFocusRequestRef.current = plan.requestId;
        setDrawerTask(found);
      })
      .catch(() => { /* ссылка на удалённую или недоступную задачу — молча игнорируем */ });
  }, [focusTaskRequest, tasks, pagedTasks, personTasks, apiBaseUrl, buildHeaders]);

  /* Цифры «Обзора» — серверная сводка по всем видимым задачам: считать их
     на клиенте нельзя, полного списка у раздела больше нет. */
  const stats = useMemo(() => ({
    total:      Number(tasksSummary?.total || 0),
    inProgress: Number(tasksSummary?.in_progress || 0),
    completed:  Number(tasksSummary?.completed || 0),
    overdue:    Number(tasksSummary?.returned || 0),
    backlog:    Number(tasksSummary?.backlog || 0),
  }), [tasksSummary]);

  const backlogCount = stats.backlog;

  // Count badges for tabs
  const incomingAlert = useMemo(
    () => incomingTasks.filter(t => t.status === 'assigned').length,
    [incomingTasks]
  );

  useEffect(() => {
    setIncomingPersonKey(prev =>
      prev && incomingGroups.some(g => g.key === prev) ? prev : null
    );
  }, [incomingGroups]);

  useEffect(() => {
    setOutgoingPersonKey(prev =>
      prev && outgoingGroups.some(g => g.key === prev) ? prev : null
    );
  }, [outgoingGroups]);

  /* ── Бриф продакшна: чего не хватает (задача #354) ── */
  const formIssues = useCallback((values, modal = null) => (
    values.tag === PRODUCTION_TAG
      ? productionBriefIssues(values.productionBrief, values.description, {
        productionAllowed: productionAllowedFor(
          values.assigneeIds,
          // Исполнители доски — тоже те, кому можно поручить (26.11.2026): маркетолога
          // среди них узнаём по отделу, даже если его нет в получателях.
          [...recipients, ...boardAssignees(findProject(projects, values.projectId))],
          modal ? { tag: modal.initialTag, assigneeIds: modal.initialAssigneeIds } : null
        ),
      })
      : []
  ), [recipients, projects]);
  /* Проект обязателен, когда выбирать есть из чего: задача без доски у
     человека с досками — это задача, которую никто из отдела не увидит. */
  const composerProjects = useMemo(() => creatableProjects(projects), [projects]);
  const createBaseIssues = useMemo(() => [
    ...(composerProjects.length && !form.projectId
      ? [{ key: 'project', text: 'Выберите проект — задача попадёт на его доску' }]
      : []),
    ...formIssues(form),
  ], [composerProjects, formIssues, form]);
  const editBaseIssues = useMemo(() => formIssues(editForm, editModal), [formIssues, editForm, editModal]);
  /* Срок в прошлом сверяется с часами на каждой отрисовке и ещё раз при
     сохранении, а не запоминается вместе с формой: форму со сроком «сегодня,
     18:00», открытую в 17:50, могут сохранить и в 18:10. */
  const createIssues = [...createBaseIssues, ...deadlineIssues(form)];
  const editIssues = [...editBaseIssues, ...deadlineIssues(editForm)];
  const createIssueNav = useBriefIssueNav(createBodyRef, createIssues.map((issue) => issue.key));
  const editIssueNav = useBriefIssueNav(editBodyRef, editIssues.map((issue) => issue.key));
  const createIssueFor = useCallback(
    (key) => (createIssuesShown ? createIssues.find((issue) => issue.key === key)?.text || '' : ''),
    [createIssuesShown, createIssues]
  );
  const editIssueFor = useCallback(
    (key) => (editIssuesShown ? editIssues.find((issue) => issue.key === key)?.text || '' : ''),
    [editIssuesShown, editIssues]
  );
  useEffect(() => { if (!createOpen) setCreateIssuesShown(false); }, [createOpen]);
  /* Открыли форму — подставляем доску, если человек её не выбирал сам. Проект,
     подставленный прошлым открытием, тоже меняем: человек закрыл форму на доске
     HR, перешёл на FIN — новая задача должна пойти в FIN, а не «прилипнуть». */
  useEffect(() => {
    if (!createOpen || !composerDefaultProject) return;
    setForm((prev) => {
      if (prev.projectId && !prev.projectAuto) return prev;
      if (prev.projectId === composerDefaultProject && (prev.sectionId || '') === composerDefaultSection) return prev;
      const nextProject = findProject(projects, composerDefaultProject);
      return {
        ...prev,
        projectId: composerDefaultProject,
        projectAuto: true,
        sectionId: composerDefaultSection,
        watcherIds: keepWatchersForProject(prev.watcherIds, nextProject),
        // Исполнители — как при выборе доски руками: только люди этой доски
        // (24.11.2026). Та же доска — состав не трогаем.
        ...(prev.projectId === composerDefaultProject ? {} : assigneesAfterProjectChange(prev, nextProject, {
          currentUserId,
          allowedIds: prev.tag === PRODUCTION_TAG
            ? productionPersonIds([...recipients, ...boardAssignees(nextProject)])
            : null,
          // Пока получатели грузятся (список пуст), никого не выкидываем.
          recipientIds: recipients.length ? new Set(recipients.map((person) => String(person.id))) : null,
        })),
      };
    });
  }, [createOpen, composerDefaultProject, composerDefaultSection, projects, currentUserId, recipients]);
  useEffect(() => { if (!editModal.open) setEditIssuesShown(false); }, [editModal.open]);

  /* ── Create ── */
  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.subject.trim()) { notify('Укажите тему задачи', 'error'); return; }
    if (!(form.assigneeIds || []).length) { notify('Выберите сотрудника', 'error'); return; }
    if (createBaseIssues.length || deadlineIssues(form).length) {
      setCreateIssuesShown(true);
      createIssueNav.first();
      return;
    }

    const body = new FormData();
    appendTaskFormData(body, form);
    selectedFiles.forEach(f => body.append('files', f));

    setIsCreateLoading(true);
    try {
      const res = await axios.post(`${apiBaseUrl}/api/tasks`, body, { headers: buildHeaders() });
      const createdKey = res?.data?.key;
      if (createdKey) {
        notify(form.isBacklog ? `Задача ${createdKey} добавлена в бэклог` : `Задача ${createdKey} поставлена`);
      } else {
        notify(res?.data?.message || 'Задача создана');
      }
      if (res?.data?.warning) notify(res.data.warning, 'error');
      if (form.projectId) writeStoredValue(composerProjectStorageKey(user?.id), form.projectId);
      setForm(buildEmptyTaskForm());
      setSelectedFiles([]);
      setCreateOpen(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
      await refreshTasksData();
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось создать задачу', 'error');
    } finally { setIsCreateLoading(false); }
  };

  /* ── Edit / Delete ── */
  const openEditModal = useCallback((task) => {
    if (!task?.id) return;
    const nextForm = taskToTaskForm(task);
    // Задача поставлена всем исполнителям доски — правка открывается на «Все» (24.11.2026).
    const taskProject = findProject(projects, nextForm.projectId);
    const productionIds = nextForm.tag === PRODUCTION_TAG
      ? productionPersonIds([...recipients, ...boardAssignees(taskProject)])
      : null;
    setEditForm({
      ...nextForm,
      assignAll: isAllBoardAssigneesSelection(nextForm.assigneeIds, taskProject, { currentUserId, allowedIds: productionIds }),
    });
    setEditModal({
      open: true,
      taskId: task.id,
      taskSubject: task.subject || '',
      // С чем задача пришла в правку: продакшн с прежним составом сервер
      // правит без сверки отдела (маркетолог мог с тех пор сменить отдел).
      initialTag: task.tag || 'task',
      initialAssigneeIds: taskAssigneeIds(task).map(String),
    });
  }, [projects, recipients, currentUserId]);

  const closeEditModal = useCallback(() => {
    setEditModal({ open: false, taskId: null, taskSubject: '' });
    setEditForm(buildEmptyTaskForm());
  }, []);

  const submitEditTask = useCallback(async (e) => {
    e.preventDefault();
    if (!editModal.taskId) return;
    if (!editForm.subject.trim()) { notify('Укажите тему задачи', 'error'); return; }
    if (!(editForm.assigneeIds || []).length) { notify('Выберите исполнителя', 'error'); return; }
    if (editBaseIssues.length || deadlineIssues(editForm).length) {
      setEditIssuesShown(true);
      editIssueNav.first();
      return;
    }

    const key = `${editModal.taskId}:edit`;
    setActionLoadingKey(key);
    try {
      const payload = buildTaskJsonPayload(editForm);
      const res = await axios.patch(
        `${apiBaseUrl}/api/tasks/${editModal.taskId}`,
        payload,
        { headers: buildHeaders() }
      );
      notify(res?.data?.message || 'Задача обновлена');
      if (res?.data?.warning) notify(res.data.warning, 'error');
      closeEditModal();
      await refreshTasksData();
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось обновить задачу', 'error');
    } finally {
      setActionLoadingKey('');
    }
  }, [editModal, editForm, editBaseIssues, editIssueNav, apiBaseUrl, buildHeaders, notify, closeEditModal, refreshTasksData]);

  /* Наблюдатели из карточки — одним PATCH, только этим полем: остальное в задаче
     не трогаем (правит её тот же постановщик, сервер сверит доступ к доске). */
  const saveTaskWatchers = useCallback(async (task, watcherIds) => {
    const taskId = Number(task?.id || 0);
    if (!taskId) return false;
    try {
      await axios.patch(
        `${apiBaseUrl}/api/tasks/${taskId}`,
        { watcher_ids: (watcherIds || []).map(Number).filter((id) => id > 0) },
        { headers: buildHeaders() }
      );
      notify('Наблюдатели сохранены');
      await refreshTasksData();
      return true;
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось сохранить наблюдателей', 'error');
      return false;
    }
  }, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);

  const openDeleteModal = useCallback((task) => {
    if (!task?.id) return;
    setDeleteModal({
      open: true,
      taskId: task.id,
      taskSubject: (task.subject || `#${task.id}`).trim(),
    });
  }, []);

  const closeDeleteModal = useCallback(() => {
    setDeleteModal({ open: false, taskId: null, taskSubject: '' });
  }, []);

  const submitDeleteTask = useCallback(async (e) => {
    if (e?.preventDefault) e.preventDefault();
    if (!deleteModal.taskId) return;

    const taskId = deleteModal.taskId;
    const key = `${taskId}:delete`;
    setActionLoadingKey(key);
    try {
      const res = await axios.delete(
        `${apiBaseUrl}/api/tasks/${taskId}`,
        { headers: buildHeaders() }
      );
      notify(res?.data?.message || 'Задача удалена');
      if (res?.data?.warning) notify(res.data.warning, 'error');
      closeDeleteModal();
      setDrawerTask(prev => (prev?.id === taskId ? null : prev));
      if (Number(pinnedTaskId) === Number(taskId)) onUnpinTask?.();
      await refreshTasksData();
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось удалить задачу', 'error');
    } finally {
      setActionLoadingKey('');
    }
  }, [deleteModal.taskId, apiBaseUrl, buildHeaders, notify, closeDeleteModal, refreshTasksData, pinnedTaskId, onUnpinTask]);

  /* ── Update status ── */
  const updateStatus = useCallback(async (taskId, action, options = {}) => {
    const comment = String(options?.comment || '').trim();
    const files = Array.isArray(options?.files) ? options.files.filter(Boolean) : [];
    const useMultipart = files.length > 0 || action === 'returned' || action === 'reopened';
    const key = `${taskId}:${action}`;
    setActionLoadingKey(key);
    try {
      const payload = useMultipart
        ? (() => {
          const body = new FormData();
          body.append('action', action);
          body.append('comment', comment);
          files.forEach(file => body.append('files', file));
          return body;
        })()
        : { action, comment };
      const res = await axios.post(
        `${apiBaseUrl}/api/tasks/${taskId}/status`,
        payload,
        { headers: buildHeaders() }
      );
      notify(res?.data?.message || 'Статус обновлён');
      if (res?.data?.warning) notify(res.data.warning, 'error');
      await refreshTasksData();
      return true;
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось обновить статус', 'error');
      return false;
    } finally { setActionLoadingKey(''); }
  }, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);

  /* ── Доска: планирующие правки (бэклог, порядок, оценка, срок) ── */
  const updateBoardItems = useCallback(async (items) => {
    if (!Array.isArray(items) || !items.length) return false;
    const key = `board:${items.map((item) => item.task_id).join(',')}`;
    setActionLoadingKey(key);
    try {
      const res = await axios.post(
        `${apiBaseUrl}/api/tasks/board`,
        { items },
        { headers: buildHeaders() }
      );
      if (res?.data?.warning) notify(res.data.warning, 'error');
      await refreshTasksData();
      // Что сервер записал на деле — по каждой задаче (непустой список и есть «да»):
      // доска и карточка задачи сверяют с ним свои оптимистичные правки.
      return Array.isArray(res?.data?.items) ? res.data.items : [];
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось обновить доску', 'error');
      return false;
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);

  /* Карточку уволенного исполнителя суперадмин переносит в любую колонку доски.
     Доска ставит её туда сразу и сверяет с тем, что сервер записал (task). */
  const moveBoardTask = useCallback(async (task, column) => {
    const taskId = Number(task?.id || 0);
    if (!taskId) return false;
    setActionLoadingKey(`${taskId}:move`);
    try {
      const res = await axios.post(
        `${apiBaseUrl}/api/tasks/${taskId}/move`,
        { column },
        { headers: buildHeaders() }
      );
      await refreshTasksData();
      return res?.data?.task || false;
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось перенести задачу', 'error');
      // Отказ значит, что доска знает о задаче устаревшее: исполнителя вернули на
      // работу (409), задачу удалили. Перечитываем — иначе каждая следующая попытка
      // снова уходила бы в отказ. Карточка уже на месте, ждать перечитки незачем.
      refreshTasksData().catch(() => {});
      return false;
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);

  /* ── Отчёты о проделанной работе ── */
  const submitTaskReport = useCallback(async (task, { body, spentMinutes, kind = 'progress' }) => {
    const taskId = Number(task?.id || 0);
    const text = String(body || '').trim();
    if (!taskId) return false;
    if (!text) { notify('Опишите проделанную работу', 'error'); return false; }
    const key = `${taskId}:report`;
    setActionLoadingKey(key);
    try {
      const res = await axios.post(
        `${apiBaseUrl}/api/tasks/${taskId}/reports`,
        { body: text, spent_minutes: spentMinutes || 0, kind },
        { headers: buildHeaders() }
      );
      notify(res?.data?.message || 'Отчёт добавлен');
      if (res?.data?.warning) notify(res.data.warning, 'error');
      await refreshTasksData();
      return true;
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось сохранить отчёт', 'error');
      return false;
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);

  const patchTaskReport = useCallback(async (task, report, { body, spentMinutes }) => {
    const reportId = Number(report?.id || 0);
    if (!reportId) return false;
    const text = String(body || '').trim();
    if (!text) { notify('Опишите проделанную работу', 'error'); return false; }
    const key = `${task?.id}:report:${reportId}`;
    setActionLoadingKey(key);
    try {
      const res = await axios.patch(
        `${apiBaseUrl}/api/tasks/reports/${reportId}`,
        { body: text, spent_minutes: spentMinutes || 0 },
        { headers: buildHeaders() }
      );
      notify(res?.data?.message || 'Отчёт обновлён');
      await refreshTasksData();
      return true;
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось обновить отчёт', 'error');
      return false;
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);

  const removeTaskReport = useCallback(async (task, report) => {
    const reportId = Number(report?.id || 0);
    if (!reportId) return false;
    const key = `${task?.id}:report:${reportId}`;
    setActionLoadingKey(key);
    try {
      const res = await axios.delete(
        `${apiBaseUrl}/api/tasks/reports/${reportId}`,
        { headers: buildHeaders() }
      );
      notify(res?.data?.message || 'Отчёт удалён');
      await refreshTasksData();
      return true;
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось удалить отчёт', 'error');
      return false;
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);

  /* Уточнение по задаче: дополнение постановки, запрос информации либо ответ.
     Файлы едут только формой — иначе сервер получит JSON без вложений. */
  const submitTaskMessage = useCallback(async (task, { kind = 'note', body, files = [] } = {}) => {
    const taskId = Number(task?.id || 0);
    if (!taskId) return false;
    const text = String(body || '').trim();
    const fileList = filesFromList(files);
    if (!text && !fileList.length) {
      notify('Напишите текст или приложите файл', 'error');
      return false;
    }
    const refusal = taskUploadRefusal(fileList);
    if (refusal) { notify(refusal, 'error'); return false; }
    setActionLoadingKey(`${taskId}:message`);
    try {
      let res;
      if (fileList.length) {
        const form = new FormData();
        form.append('kind', kind);
        form.append('body', text);
        fileList.forEach((file) => form.append('files', file));
        res = await axios.post(`${apiBaseUrl}/api/tasks/${taskId}/messages`, form, { headers: buildHeaders() });
      } else {
        res = await axios.post(
          `${apiBaseUrl}/api/tasks/${taskId}/messages`,
          { kind, body: text },
          { headers: buildHeaders() }
        );
      }
      notify(res?.data?.message || 'Уточнение добавлено');
      if (res?.data?.warning) notify(res.data.warning, 'error');
      await refreshTasksData();
      return true;
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось отправить уточнение', 'error');
      return false;
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);

  const withdrawTaskInfoRequest = useCallback(async (task, message) => {
    const messageId = Number(message?.id || 0);
    if (!messageId) return false;
    setActionLoadingKey(`${Number(task?.id || 0)}:message`);
    try {
      const res = await axios.post(
        `${apiBaseUrl}/api/tasks/messages/${messageId}/withdraw`,
        {},
        { headers: buildHeaders() }
      );
      notify(res?.data?.message || 'Запрос снят');
      await refreshTasksData();
      return true;
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось снять запрос', 'error');
      return false;
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);

  /* ── Complete modal ── */
  const openCompleteModal = useCallback((task) => {
    if (!task?.id) return;
    setCompletionSummary(task?.completion_summary || '');
    setCompletionSpent('');
    setCompletionFiles([]);
    setCompleteModal({
      open: true,
      taskId: task.id,
      taskSubject: task.subject || '',
      estimateMinutes: Number(task?.estimate_minutes || 0) || null,
    });
  }, []);

  const closeCompleteModal = useCallback(() => {
    setCompleteModal({ open: false, taskId: null, taskSubject: '', estimateMinutes: null });
    setCompletionSummary('');
    setCompletionSpent('');
    setCompletionFiles([]);
  }, []);

  const openStatusModal = useCallback((task, action) => {
    if (!task?.id) return;
    if (action !== 'returned' && action !== 'reopened') return;
    setStatusComment('');
    setStatusFiles([]);
    setStatusModal({ open: true, taskId: task.id, action, taskSubject: task.subject || '' });
  }, []);

  const closeStatusModal = useCallback(() => {
    setStatusModal({ open: false, taskId: null, action: '', taskSubject: '' });
    setStatusComment('');
    setStatusFiles([]);
  }, []);

  const submitComplete = useCallback(async (e) => {
    e.preventDefault();
    if (!completeModal.taskId) return;
    if (!completionSummary.trim()) { notify('Опишите проделанную работу', 'error'); return; }
    const key = `${completeModal.taskId}:completed`;
    setActionLoadingKey(key);
    try {
      const body = new FormData();
      body.append('action', 'completed');
      body.append('completion_summary', completionSummary.trim());
      body.append('spent_minutes', String(parseSpentInput(completionSpent) || 0));
      completionFiles.forEach(f => body.append('files', f));
      const res = await axios.post(
        `${apiBaseUrl}/api/tasks/${completeModal.taskId}/status`,
        body,
        { headers: buildHeaders() }
      );
      notify(res?.data?.message || 'Задача выполнена');
      if (res?.data?.warning) notify(res.data.warning, 'error');
      closeCompleteModal();
      await refreshTasksData();
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось завершить задачу', 'error');
    } finally { setActionLoadingKey(''); }
  }, [completeModal, completionSummary, completionSpent, completionFiles, apiBaseUrl, buildHeaders, notify, closeCompleteModal, refreshTasksData]);

  const submitStatusModal = useCallback(async (e) => {
    e.preventDefault();
    if (!statusModal.taskId || !statusModal.action) return;
    const ok = await updateStatus(statusModal.taskId, statusModal.action, {
      comment: statusComment,
      files: statusFiles,
    });
    if (ok) closeStatusModal();
  }, [statusModal, statusComment, statusFiles, updateStatus, closeStatusModal]);

  /* ── Файлы вложений ── */
  /* Один запрос на файл и для просмотра, и для скачивания: скачанный файл
     остаётся в памяти вкладки (previewCache), и «Скачать» после просмотра
     не тянет его с сервера ещё раз. */
  const loadAttachmentFile = useCallback(async (att, signal) => {
    const res = await axios.get(
      `${apiBaseUrl}/api/tasks/attachments/${att.id}/download`,
      { headers: buildHeaders(), responseType: 'blob', signal }
    );
    return res.data;
  }, [apiBaseUrl, buildHeaders]);

  const downloadAttachment = useCallback(async (att) => {
    const key = taskFileKey(att);
    try {
      let blob = previewCache.get(key);
      if (!blob) {
        blob = await loadAttachmentFile(att);
        previewCache.put(key, blob);
      }
      saveBlobAs(blob, att.file_name || `attachment-${att.id}`);
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось скачать файл', 'error');
    }
  }, [loadAttachmentFile, notify]);

  /* Адреса картинок карточки — один запрос на карточку (useTaskPhotoPreviews). */
  const loadTaskPhotos = useCallback(async (taskId) => {
    const res = await axios.get(
      `${apiBaseUrl}/api/tasks/${taskId}/photos`,
      { headers: buildHeaders() }
    );
    return Array.isArray(res?.data?.photos) ? res.data.photos : [];
  }, [apiBaseUrl, buildHeaders]);

  /* ── Action buttons ── */
  const getActionButtons = useCallback((task) => {
    return buildTaskActionButtons(task, currentUserId, currentUserRole);
  }, [currentUserId, currentUserRole]);

  /* Смена статуса с доски: действия с обязательным комментарием уходят в свои модалки. */
  const handleBoardStatusAction = useCallback((task, action) => {
    if (action === 'completed') {
      openCompleteModal(task);
      return;
    }
    if (action === 'returned' || action === 'reopened') {
      openStatusModal(task, action);
      return;
    }
    updateStatus(task.id, action);
  }, [openCompleteModal, openStatusModal, updateStatus]);

  /* Перенос задачи в бэклог кнопкой — то же действие, что и перетаскивание
     карточки в колонку «Бэклог», но доступное из карточки задачи. */
  const moveTaskToBacklog = useCallback(async (task) => {
    const taskId = Number(task?.id || 0);
    if (!taskId) return;
    const ok = await updateBoardItems([{ task_id: taskId, is_backlog: true }]);
    if (ok) notify('Задача убрана в бэклог');
  }, [updateBoardItems, notify]);

  /* Балл приоритета (1–5) — только суперадмины, тем же пакетным вызовом доски.
     В карточке он меняется сразу, а если сервер откажет — возвращается прежний.
     Первый балл у задачи из «Новой» переводит её в бэклог, снятый балл — обратно в
     «Новую» (то же правило у сервера). Об этом говорим: в открытой карточке
     переезда не видно. */
  const setTaskPriorityScore = useCallback(async (task, score) => {
    const taskId = Number(task?.id || 0);
    if (!taskId) return;
    const previous = priorityScoreOf(task);
    const wasBacklog = Boolean(task?.is_backlog);
    const move = scoreBoardMove(task, score);
    patchTaskEverywhere(taskId, (current) => ({
      ...current,
      priority_score: score,
      ...(move ? { is_backlog: move === 'backlog' } : {}),
    }), { board: false });
    const items = await updateBoardItems([{ task_id: taskId, priority_score: score }]);
    if (!items) {
      patchTaskEverywhere(taskId, (current) => ({ ...current, priority_score: previous, is_backlog: wasBacklog }), { board: false });
      return;
    }
    // Карточке задачи и тосту — то, что сервер сделал на деле: задачу могли в ту же
    // секунду взять в работу, и тогда балл записан, а переезда не было.
    const item = items.find((entry) => Number(entry?.task_id) === taskId) || null;
    if (item) {
      setDrawerTask((prev) => (Number(prev?.id || 0) === taskId
        ? { ...prev, priority_score: item.priority_score ?? null, is_backlog: Boolean(item.is_backlog) }
        : prev));
    }
    if (item?.score_move === 'to_backlog') notify('Балл поставлен — задача перешла в бэклог');
    if (item?.score_move === 'to_new') notify('Балл снят — задача вернулась в «Новую»');
  }, [patchTaskEverywhere, updateBoardItems, notify]);

  const openBacklogCreate = useCallback(() => {
    setForm(buildEmptyTaskForm({ isBacklog: true }));
    setSelectedFiles([]);
    setCreateOpen(true);
  }, []);

  const isReturnAction = statusModal.action === 'returned';
  const statusModalTitle = isReturnAction ? 'Возврат на доработку' : 'Возобновление задачи';
  const statusModalCommentLabel = isReturnAction ? 'Комментарий по доработке' : 'Комментарий к возобновлению';
  const statusModalSubmitLabel = isReturnAction ? 'Вернуть на доработку' : 'Возобновить задачу';
  const editModalLoadingKey = `${editModal.taskId}:edit`;
  const deleteModalLoadingKey = `${deleteModal.taskId}:delete`;
  const statusModalLoadingKey = `${statusModal.taskId}:${statusModal.action}`;

  // Подложки всех окон раздела: в каждом из них набирают текст, и в каждом
  // выделение с выходом за край окна закрывало его вместе с набранным.
  const closeCreate = useCallback(() => setCreateOpen(false), []);
  const createOverlayProps = useOverlayDismiss(closeCreate);
  const editOverlayProps = useOverlayDismiss(closeEditModal);
  const deleteOverlayProps = useOverlayDismiss(closeDeleteModal);
  const completeOverlayProps = useOverlayDismiss(closeCompleteModal);
  const statusOverlayProps = useOverlayDismiss(closeStatusModal);

  /* Системное «назад» закрывает окно — на телефоне это главный способ его
  закрыть. Без записи в стеке жест перешагивает через окно и снимает переход
  между разделами: человек свайпает, чтобы закрыть окно, а получает чужой
  раздел (владелец 11.09.2026: «перекидывает в другой раздел»). */
  useScreenBackGesture(isMobileShell && createOpen, closeCreate);
  useScreenBackGesture(isMobileShell && editModal.open, closeEditModal);
  useScreenBackGesture(isMobileShell && deleteModal.open, closeDeleteModal);
  useScreenBackGesture(isMobileShell && completeModal.open, closeCompleteModal);
  useScreenBackGesture(isMobileShell && statusModal.open, closeStatusModal);
  /* Карточка задачи на телефоне — тоже экран (см. TaskDrawer). */
  const closeDrawer = useCallback(() => setDrawerTask(null), []);
  useScreenBackGesture(isMobileShell && Boolean(drawerTask), closeDrawer);

  /* ── Render helpers ── */
  const renderTaskList = (list, emptyTitle, emptySub, loading = isTasksLoading) => {
    if (loading) return <SkeletonList />;
    if (!list.length) return (
      <div className="tv-empty">
        <span className="tv-empty-icon">📋</span>
        <span className="tv-empty-title">{emptyTitle}</span>
        <span className="tv-empty-sub">{emptySub}</span>
      </div>
    );
    const rows = withDateSeparators(list);
    return (
      <div className="tv-task-list">
        {rows.map((row) => {
          if (row.type === 'separator') {
            return (
              <div key={row.key} className="tv-task-date-separator">
                <span>{row.label}</span>
              </div>
            );
          }
          return (
            <TaskRow
              key={row.key}
              task={row.task}
              compact={isMobileShell}
              onClick={setDrawerTask}
              /* На телефоне скрепки в строке нет: закрепляют из карточки задачи. */
              onPin={isMobileShell ? undefined : (task) => {
                if (Number(task?.id || 0) === Number(pinnedTaskId)) {
                  onUnpinTask?.();
                  return;
                }
                onPinTask?.(task);
              }}
              isPinned={Number(row.task?.id || 0) === Number(pinnedTaskId)}
            />
          );
        })}
      </div>
    );
  };

  const renderGroupedTasks = (groups, selectedKey, onSelect, emptyTitle, emptySub, showNotAccepted = false) => {
    if (isTasksLoading) return <SkeletonList count={3} />;
    if (!groups.length) return (
      <div className="tv-empty">
        <span className="tv-empty-icon">🗂</span>
        <span className="tv-empty-title">{emptyTitle}</span>
        <span className="tv-empty-sub">{emptySub}</span>
      </div>
    );
    const selected = groups.find(g => g.key === selectedKey) || null;

    if (selected) {
      const canLoadFromServer = Number(selected?.personId || 0) > 0;
      const selectedTotal = canLoadFromServer
        ? (personTasksTotal > 0 ? personTasksTotal : selected.tasks.length)
        : selected.tasks.length;
      const selectedList = canLoadFromServer ? personTasks : selected.tasks;
      const selectedLoading = canLoadFromServer ? isPersonTasksLoading : isTasksLoading;
      return (
        <>
          <div className="tv-person-tasks-header">
            <span className="tv-person-tasks-label">{selected.name} · {selectedTotal} {pluralRu(selectedTotal, 'задача', 'задачи', 'задач')}</span>
            <button className="tv-person-tasks-back" type="button" onClick={() => onSelect(null)}>
              <BackIcon /> Назад к списку
            </button>
          </div>
          {renderTaskList(selectedList, 'Нет задач', 'У этого сотрудника пока нет задач.', selectedLoading)}
          {canLoadFromServer && !selectedLoading && selectedTotal > TASKS_PAGE_SIZE && (
            <div className="tv-pagination">
              <button
                type="button"
                className="tv-btn tv-btn-ghost"
                disabled={personTasksPage <= 1}
                onClick={() => setPersonTasksPage(v => Math.max(1, v - 1))}
              >
                Назад
              </button>
              <span className="tv-pagination-info">Страница {personTasksPage} из {personTotalPages}</span>
              <button
                type="button"
                className="tv-btn tv-btn-ghost"
                disabled={personTasksPage >= personTotalPages}
                onClick={() => setPersonTasksPage(v => Math.min(personTotalPages, v + 1))}
              >
                Далее
              </button>
            </div>
          )}
        </>
      );
    }

    /* Компьютер: люди — лентой аватаров, как закреплённые чаты в «Сообщениях»
       (владелец выбрал 16.11). Число на аватаре — задачи, которые ждут принятия:
       во «Входящих» — вашего, красным, как «Ждут вас»; в «Исходящих» — их, серым.
       Остальные счётчики — в подсказке при наведении и в подписи для скринридера.
       На телефоне — прежние строки: там свой слой (tasks-mobile.css). */
    if (!isMobileShell) {
      return (
        <div className="tv-people" role="group" aria-label={showNotAccepted ? 'Кто поставил вам задачи' : 'Кому вы поставили задачи'}>
          {groups.map((group) => {
            const waiting = Number(group.notAccepted || 0);
            const waitingLabel = showNotAccepted ? 'Ожидают принятия' : 'Ещё не приняты';
            return (
              <button
                key={group.key}
                type="button"
                className="tv-people-item"
                onClick={() => onSelect(group.key)}
                onMouseEnter={alignPeopleTip}
                onFocus={alignPeopleTip}
                aria-label={`${group.name}: ${waitingLabel.toLowerCase()} ${waiting}, в работе ${group.active}, выполнено ${group.done}`}
              >
                <span className="tv-people-avatar" aria-hidden="true">
                  <AvatarCircle className="tv-avatar-md" name={group.name} avatarUrl={group.avatarUrl} />
                  {waiting > 0 && (
                    <span className={`tv-people-badge ${showNotAccepted ? 'is-alert' : ''}`}>
                      {waiting > 99 ? '99+' : waiting}
                    </span>
                  )}
                </span>
                <span className="tv-people-name" aria-hidden="true">{group.name}</span>
                <span className="tv-people-tip" aria-hidden="true">
                  <span className="tv-people-tip-name">{group.name}</span>
                  <span className="tv-people-tip-row"><span>{waitingLabel}</span><b>{waiting}</b></span>
                  <span className="tv-people-tip-row"><span>В работе</span><b>{group.active}</b></span>
                  <span className="tv-people-tip-row"><span>Выполнено</span><b>{group.done}</b></span>
                </span>
              </button>
            );
          })}
        </div>
      );
    }

    return (
      <div className="tv-person-list">
        {groups.map(group => (
          <button
            key={group.key}
            type="button"
            className="tv-person-row"
            onClick={() => onSelect(group.key)}
          >
            <AvatarCircle className="tv-avatar-md" name={group.name} avatarUrl={group.avatarUrl} />
            <span className="tv-person-info">
              <span className="tv-person-name">{group.name}</span>
              <span className="tv-person-stats">
                <span className="tv-person-stat-item">
                  <span className="tv-task-count">{group.done}</span> выполнено
                </span>
                <span className="tv-person-stat-item">
                  <span className="tv-task-count">{group.active}</span> в работе
                </span>
                {showNotAccepted && (
                  <span className="tv-alert-stat">
                    {group.notAccepted > 0 && <span className="tv-pulse-dot" />}
                    <span className={`tv-task-count ${group.notAccepted > 0 ? 'is-alert' : ''}`}>{group.notAccepted}</span>
                    ожидают принятия
                  </span>
                )}
              </span>
            </span>
            <ChevronRight />
          </button>
        ))}
      </div>
    );
  };

  if (!user || !canAccessTasks) return null;

  const hasActiveFilters = Boolean(searchQuery.trim() || filterStatus || filterTag || filterPriority);
  const isAnyTasksLoading = isTasksLoading || isPagedTasksLoading || isPersonTasksLoading;


  return (
    <div className={`tv-root ${workspaceTab === 'overview' ? '' : 'is-board-mode'}`}>
      {/* Top bar */}
      <div className="tv-topbar">
        <div className="tv-topbar-lead">
          <h1 className="tv-topbar-title">Задачи</h1>
          {/* Доска проекта (задача #401). «Мои задачи» переключатель не сужает:
              там задачи и так всегда ваши. */}
          {showProjectSwitcher && (
            <CustomSelect
              className="tv-project-select"
              variant="ios"
              ariaLabel="Проект"
              value={projectFilter ?? TASK_PROJECT_ALL}
              options={projectOptions}
              onChange={changeProjectFilter}
              disabled={!isProjectResolved}
              searchable
              searchPlaceholder="Найти проект…"
            />
          )}
          {/* Разделы открытой доски — только у досок, где они заведены. */}
          {showSectionSwitcher && (
            <CustomSelect
              className="tv-section-select"
              variant="ios"
              ariaLabel="Раздел доски"
              value={effectiveSectionFilter}
              options={sectionOptions}
              onChange={changeSectionFilter}
              searchable={sectionOptions.length > 8}
              searchPlaceholder="Найти раздел…"
            />
          )}
          {/* Состав досок настраивает тот, кто в разделе и так видит всё. На
              телефоне не показываем: это работа за компьютером, а шапке там тесно. */}
          {projectsMeta.canManage && !isMobileShell && (
            <button
              type="button"
              className="tv-icon-btn"
              title="Настроить проекты"
              aria-label="Настроить проекты"
              onClick={() => setProjectsSheetOpen(true)}
            >
              <Settings size={15} strokeWidth={2} />
            </button>
          )}
        </div>
        <div className="tv-topbar-actions">
          <TaskInbox
            needs={actionNeeds}
            unseenCount={actionNeedsUnseen}
            onOpen={openActionNeed}
            onMarkAllSeen={markActionNeedsSeen}
            compact={isMobileShell}
          />
          {isMobileShell ? (
            /* Телефон: круглые значки рядом с крупным заголовком, как в шапке
               приложения. «Обновить» здесь нет: раздел перечитывает задачи при
               каждом входе в него, а четвёртая кнопка — это та самая вторая
               строка кнопок над списком, от которой уходили. */
            <>
              <button
                type="button"
                className={`tv-round-btn ${notesOpen ? 'is-active' : ''}`}
                aria-label="Заметки"
                onClick={() => setNotesOpen((prev) => !prev)}
              >
                <StickyNote size={18} strokeWidth={1.9} />
              </button>
              <button
                type="button"
                className="tv-round-btn is-primary"
                aria-label="Новая задача"
                onClick={() => setCreateOpen(true)}
              >
                <PlusIcon />
              </button>
            </>
          ) : (
            <>
              {/* Компьютер: «Заметки» и «Обновить» — круглые кнопки-значки, как на
                  панели iOS; подписи — в подсказке и для скринридера. */}
              <button
                className={`tv-round-btn ${notesOpen ? 'is-active' : ''}`}
                type="button"
                title="Заметки"
                aria-label="Заметки"
                onClick={() => setNotesOpen((prev) => !prev)}
              >
                <StickyNote size={18} strokeWidth={1.9} />
              </button>
              <button
                className={`tv-round-btn ${isAnyTasksLoading ? 'is-spinning' : ''}`}
                type="button"
                title={isAnyTasksLoading ? 'Обновляю…' : 'Обновить'}
                aria-label={isAnyTasksLoading ? 'Обновляю…' : 'Обновить'}
                onClick={refreshTasksData}
                disabled={isAnyTasksLoading}
              >
                <RefreshCw size={17} strokeWidth={1.9} />
              </button>
              <button className="tv-btn tv-btn-primary" onClick={() => setCreateOpen(true)}>
                <PlusIcon /> Новая задача
              </button>
            </>
          )}
        </div>
      </div>

      {projectsMeta.canManage && (
        <TaskProjectsSheet
          open={projectsSheetOpen}
          onClose={() => setProjectsSheetOpen(false)}
          projects={projects}
          people={projectsMeta.candidates.length ? projectsMeta.candidates : recipients}
          roleLabels={ROLE_LABELS}
          apiBaseUrl={apiBaseUrl}
          buildHeaders={buildHeaders}
          notify={notify}
          // Сменили ключ доски — у загруженных карточек ключи старые (HR-125 → HRD-125):
          // перечитываем и задачи, а не только список досок.
          onSaved={async ({ codeChanged } = {}) => {
            await fetchProjects();
            if (codeChanged) await refreshTasksData();
          }}
        />
      )}

      {/* Заметки — отдельное широкое окно, а не блок, вклеенный в страницу. */}
      <FullscreenSheet
        open={notesOpen}
        wide
        icon="fa-file-lines"
        title="Заметки"
        subtitle={notesState?.backendReady
          ? 'Личные заметки — сохраняются в базе данных'
          : 'Сервер недоступен, включён локальный режим'}
        onClose={() => setNotesOpen(false)}
      >
        <TaskNotesPanel notesState={notesState} wide />
      </FullscreenSheet>

      {/* Обзор / Бэклог / Доска / Таймлайн */}
      <div className="tv-workspace-tabs">
        {WORKSPACE_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`tv-workspace-tab ${workspaceTab === tab.id ? 'is-active' : ''}`}
            onClick={() => selectWorkspaceTab(tab.id)}
          >
            {tab.label}
            {tab.id === 'backlog' && backlogCount > 0 && (
              <span className="tv-workspace-tab-count">{backlogCount}</span>
            )}
          </button>
        ))}
      </div>

      {workspaceTab !== 'overview' && !isProjectResolved && (
        <div className="tv-section">
          {/* Доску не монтируем, пока не знаем проект: иначе она успела бы
              сходить за всеми задачами и тут же перезагрузиться на свою доску. */}
          <div className="space-y-2">
            {[0, 1, 2].map((index) => (
              <div key={index} className="h-14 animate-pulse rounded-2xl bg-slate-100" />
            ))}
          </div>
        </div>
      )}

      {workspaceTab !== 'overview' && isProjectResolved && (
        <div className="tv-section">
          <TaskBoardWorkspace
            mode={workspaceTab}
            people={boardPeople}
            projectId={projectFilter}
            sectionId={effectiveSectionFilter}
            loadTasks={loadBoardTasks}
            reloadToken={boardReloadToken}
            taskPatches={taskPatches}
            currentUserId={currentUserId}
            isAdmin={isAdminLikeRole(currentUserRole)}
            isSupervisor={isSupervisorRole(currentUserRole)}
            canScore={normalizeRole(currentUserRole) === 'super_admin'}
            isSuperAdmin={normalizeRole(currentUserRole) === 'super_admin'}
            focusRequest={boardFocus}
            actionNeedOf={actionNeedOf}
            isTaskOpen={Boolean(drawerTask)}
            onOpenTask={setDrawerTask}
            onStatusAction={handleBoardStatusAction}
            onBoardUpdate={updateBoardItems}
            onMoveTask={moveBoardTask}
            onCreateBacklogItem={openBacklogCreate}
            onExport={exportBoardTasks}
            notify={notify}
          />
        </div>
      )}

      {workspaceTab === 'overview' && (
      <>
      {/* Stats strip */}
      {/* Виджеты, как смарт-списки «Напоминаний»: цветной кружок со значком,
          число справа, подпись снизу. Цвет — у значка, число нейтральное. */}
      <div className="tv-stats-strip">
        <div className="tv-stat-card is-total">
          <span className="tv-stat-icon" aria-hidden="true"><Layers size={17} strokeWidth={2.2} /></span>
          <span className="tv-stat-label">Всего задач</span>
          <span className="tv-stat-value">{stats.total}</span>
        </div>
        <div className="tv-stat-card is-amber">
          <span className="tv-stat-icon" aria-hidden="true"><Timer size={17} strokeWidth={2.2} /></span>
          <span className="tv-stat-label">В работе</span>
          <span className="tv-stat-value">{stats.inProgress}</span>
        </div>
        <div className="tv-stat-card is-emerald">
          <span className="tv-stat-icon" aria-hidden="true"><CheckCircle2 size={17} strokeWidth={2.2} /></span>
          <span className="tv-stat-label">Выполнено</span>
          <span className="tv-stat-value">{stats.completed}</span>
        </div>
        <div className="tv-stat-card is-indigo is-returned">
          <span className="tv-stat-icon" aria-hidden="true"><Undo2 size={17} strokeWidth={2.2} /></span>
          <span className="tv-stat-label">Возвращено</span>
          <span className="tv-stat-value">{stats.overdue}</span>
        </div>
        <div className="tv-stat-card is-backlog">
          <span className="tv-stat-icon" aria-hidden="true"><ListTodo size={17} strokeWidth={2.2} /></span>
          <span className="tv-stat-label">В бэклоге</span>
          <span className="tv-stat-value">{stats.backlog}</span>
        </div>
      </div>

      {/* My tasks */}
      <div className="tv-section">
        <div className="tv-section-header">
          <span className="tv-section-title heading">Мои задачи</span>
        </div>
        <div className="tv-my-tabs">
          <button
            type="button"
            className={`tv-tab-btn ${myTasksTab === 'incoming' ? 'is-active' : ''}`}
            onClick={() => { setMyTasksTab('incoming'); setIncomingPersonKey(null); }}
          >
            Принятые
            {incomingAlert > 0 && (
              <span className="tv-task-count is-alert">{incomingAlert}</span>
            )}
            {incomingAlert === 0 && incomingTasks.length > 0 && (
              <span className="tv-task-count">{incomingTasks.length}</span>
            )}
          </button>
          <button
            type="button"
            className={`tv-tab-btn ${myTasksTab === 'outgoing' ? 'is-active' : ''}`}
            onClick={() => { setMyTasksTab('outgoing'); setOutgoingPersonKey(null); }}
          >
            Исходящие
            {outgoingTasks.length > 0 && (
              <span className="tv-task-count">{outgoingTasks.length}</span>
            )}
          </button>
        </div>
        {myTasksTab === 'incoming'
          ? renderGroupedTasks(
              incomingGroups, incomingPersonKey, setIncomingPersonKey,
              'Нет принятых задач', 'Задачи, назначенные вам, появятся здесь.', true
            )
          : renderGroupedTasks(
              outgoingGroups, outgoingPersonKey, setOutgoingPersonKey,
              'Нет исходящих задач', 'Задачи, созданные вами, появятся здесь.'
            )}
      </div>

      {/* All tasks */}
      {!isPersonDrilldown && (
        <div className="tv-section">
          <div className="tv-section-header">
            <span className="tv-section-title heading">Все задачи</span>
          </div>

          {/* Toolbar: search + filters */}
          <div className="tv-toolbar">
            <div className="tv-search-wrap">
              <span className="tv-search-icon"><SearchIcon /></span>
              <input
                ref={searchRef}
                className="tv-search-input"
                placeholder={isMobileShell ? 'Поиск по задачам' : 'Поиск по всем задачам (Ctrl+K)'}
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  setAllTasksPage(1);
                }}
              />
            </div>
            {!isMobileShell ? (
              <div className="tv-filter-menus">
                <TaskFilterMenu
                  title="Статус"
                  allLabel="Все статусы"
                  value={filterStatus}
                  options={Object.entries(STATUS_META).map(([key, meta]) => ({ value: key, label: meta.label }))}
                  onChange={(next) => { setFilterStatus(next); setAllTasksPage(1); }}
                />
                <TaskFilterMenu
                  title="Тип"
                  allLabel="Все типы"
                  value={filterTag}
                  options={Object.entries(TAG_META).map(([key, meta]) => ({ value: key, label: meta.label }))}
                  onChange={(next) => { setFilterTag(next); setAllTasksPage(1); }}
                />
                <TaskFilterMenu
                  title="Срочность"
                  allLabel="Любая срочность"
                  value={filterPriority}
                  options={Object.entries(PRIORITY_META)
                    .filter(([key]) => key !== 'normal')
                    .map(([key, meta]) => ({ value: key, label: meta.label }))}
                  onChange={(next) => { setFilterPriority(next); setAllTasksPage(1); }}
                />
                {hasActiveFilters && (
                  <button
                    type="button"
                    className="tv-filter-reset"
                    onClick={() => {
                      setSearchQuery('');
                      setFilterStatus('');
                      setFilterTag('');
                      setFilterPriority('');
                      setAllTasksPage(1);
                    }}
                  >
                    Сбросить
                  </button>
                )}
              </div>
            ) : (
            <div className="tv-filter-chips">
              {Object.entries(STATUS_META).map(([key, meta]) => (
                <button
                  key={key}
                  type="button"
                  className={`tv-chip ${filterStatus === key ? meta.chipCls : ''}`}
                  onClick={() => {
                    setFilterStatus(v => v === key ? '' : key);
                    setAllTasksPage(1);
                  }}
                >
                  {meta.label}
                </button>
              ))}
              {Object.entries(TAG_META).map(([key, meta]) => (
                <button
                  key={key}
                  type="button"
                  className={`tv-chip ${filterTag === key ? 'is-active' : ''}`}
                  onClick={() => {
                    setFilterTag(v => v === key ? '' : key);
                    setAllTasksPage(1);
                  }}
                >
                  {meta.label}
                </button>
              ))}
              {Object.entries(PRIORITY_META).filter(([key]) => key !== 'normal').map(([key, meta]) => (
                <button
                  key={key}
                  type="button"
                  className={`tv-chip ${filterPriority === key ? meta.chipCls : ''}`}
                  onClick={() => {
                    setFilterPriority(v => v === key ? '' : key);
                    setAllTasksPage(1);
                  }}
                >
                  {meta.label}
                </button>
              ))}
              {hasActiveFilters && (
                <button
                  type="button"
                  className="tv-chip tv-chip-reset"
                  style={{ color: 'var(--rose)', borderColor: '#fecdd3' }}
                  onClick={() => {
                    setSearchQuery('');
                    setFilterStatus('');
                    setFilterTag('');
                    setFilterPriority('');
                    setAllTasksPage(1);
                  }}
                >
                  × Сбросить
                </button>
              )}
            </div>
            )}
          </div>

          {!isPagedTasksLoading && allTasksTotal > 0 && (
            <div className="tv-results-info">
              {hasActiveFilters
                ? `Найдено: ${filteredTasksTotal} из ${allTasksTotal}`
                : `Всего: ${allTasksTotal}`}
            </div>
          )}

          {renderTaskList(
            pagedTasks,
            hasActiveFilters ? 'Ничего не найдено' : 'Задач пока нет',
            hasActiveFilters ? 'Попробуйте изменить фильтры или поисковый запрос.' : 'Создайте первую задачу, нажав кнопку «Новая задача».',
            isPagedTasksLoading
          )}

          {!isPagedTasksLoading && filteredTasksTotal > TASKS_PAGE_SIZE && (
            <div className="tv-pagination">
              <button
                type="button"
                className="tv-btn tv-btn-ghost"
                disabled={allTasksPage <= 1}
                onClick={() => setAllTasksPage(v => Math.max(1, v - 1))}
              >
                Назад
              </button>
              <span className="tv-pagination-info">Страница {allTasksPage} из {totalPages}</span>
              <button
                type="button"
                className="tv-btn tv-btn-ghost"
                disabled={allTasksPage >= totalPages}
                onClick={() => setAllTasksPage(v => Math.min(totalPages, v + 1))}
              >
                Далее
              </button>
            </div>
          )}
        </div>
      )}
      </>
      )}

      {/* Drawer */}
      {drawerTask && (
        <TaskDrawer
          task={drawerTask}
          onClose={closeDrawer}
          isMobileShell={isMobileShell}
          actionLoadingKey={actionLoadingKey}
          getActionButtons={getActionButtons}
          openCompleteModal={openCompleteModal}
          openStatusModal={openStatusModal}
          updateStatus={updateStatus}
          downloadAttachment={downloadAttachment}
          loadAttachmentFile={loadAttachmentFile}
          loadTaskPhotos={loadTaskPhotos}
          onOpenProduction={onOpenProduction}
          projects={projects}
          onSaveWatchers={saveTaskWatchers}
          onSetPriorityScore={setTaskPriorityScore}
          onEditTask={openEditModal}
          onDeleteTask={openDeleteModal}
          onCopyTaskLink={copyTaskLink}
          onMoveToBacklog={moveTaskToBacklog}
          onToggleChecklistItem={toggleChecklistItem}
          onSaveChecklistNote={saveChecklistNote}
          currentUserId={currentUserId}
          currentUserRole={currentUserRole}
          onSubmitReport={submitTaskReport}
          onSubmitTaskMessage={submitTaskMessage}
          onWithdrawInfoRequest={withdrawTaskInfoRequest}
          onPatchReport={patchTaskReport}
          onRemoveReport={removeTaskReport}
          onTogglePinTask={(task) => {
            if (Number(task?.id || 0) === Number(pinnedTaskId)) {
              onUnpinTask?.();
              return;
            }
            onPinTask?.(task);
          }}
          isPinned={Number(drawerTask?.id || 0) === Number(pinnedTaskId)}
        />
      )}

      {/* Create Modal */}
      {createOpen && (
        <div className="tv-modal-overlay otp-modal-root otp-modal-dim" {...createOverlayProps}>
          <div className="tv-modal tv-modal-composer otp-modal-panel" onClick={e => e.stopPropagation()}>
            <div className="tv-modal-header otp-modal-head">
              <h3 className="tv-modal-title">{form.isBacklog ? 'Новая задача в бэклог' : 'Новая задача'}</h3>
              <ModalCloseButton isMobileShell={isMobileShell} onClick={() => setCreateOpen(false)} />
            </div>
            <form onSubmit={handleCreate}>
              <div className="tv-modal-body" ref={createBodyRef}>
                <TaskComposerForm
                  values={form}
                  onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
                  recipients={recipients}
                  isRecipientsLoading={isRecipientsLoading}
                  disabled={isCreateLoading}
                  files={selectedFiles}
                  onFilesChange={setSelectedFiles}
                  showBacklogToggle
                  currentUserId={currentUserId}
                  issueFor={createIssueFor}
                  projects={projects}
                />
              </div>
              <div className="tv-modal-footer">
                <BriefIssueNav count={createIssuesShown ? createIssues.length : 0} onStep={createIssueNav.step} />
                <button type="button" className="tv-btn tv-btn-ghost tv-modal-cancel" disabled={isCreateLoading}
                  onClick={() => setCreateOpen(false)}>Отмена</button>
                <button type="submit" className="tv-btn tv-btn-primary"
                  disabled={isCreateLoading || isRecipientsLoading}>
                  {isCreateLoading ? 'Создаю...' : (form.isBacklog ? 'Добавить в бэклог' : 'Поставить задачу')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editModal.open && (
        <div className="tv-modal-overlay otp-modal-root otp-modal-dim" {...editOverlayProps}>
          <div className="tv-modal tv-modal-composer otp-modal-panel" onClick={e => e.stopPropagation()}>
            <div className="tv-modal-header otp-modal-head">
              <h3 className="tv-modal-title">Редактирование задачи</h3>
              <ModalCloseButton isMobileShell={isMobileShell} onClick={closeEditModal} />
            </div>
            <form onSubmit={submitEditTask}>
              <div className="tv-modal-body" ref={editBodyRef}>
                {editModal.taskSubject && (
                  <p className="tv-composer-context">{editModal.taskSubject}</p>
                )}
                <TaskComposerForm
                  values={editForm}
                  onChange={(patch) => setEditForm((prev) => ({ ...prev, ...patch }))}
                  recipients={recipients}
                  isRecipientsLoading={isRecipientsLoading}
                  disabled={!!actionLoadingKey}
                  currentUserId={currentUserId}
                  issueFor={editIssueFor}
                  projects={projects}
                />
              </div>
              <div className="tv-modal-footer">
                <BriefIssueNav count={editIssuesShown ? editIssues.length : 0} onStep={editIssueNav.step} />
                <button
                  type="button"
                  className="tv-btn tv-btn-ghost tv-modal-cancel"
                  disabled={!!actionLoadingKey}
                  onClick={closeEditModal}
                >
                  Отмена
                </button>
                <button
                  type="submit"
                  className="tv-btn tv-btn-primary"
                  disabled={!!actionLoadingKey || isRecipientsLoading}
                >
                  {actionLoadingKey === editModalLoadingKey ? 'Сохраняю...' : 'Сохранить изменения'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete Confirm Modal */}
      {deleteModal.open && (
        <div className="tv-modal-overlay otp-modal-root otp-modal-dim" {...deleteOverlayProps}>
          <div className="tv-modal otp-modal-panel" onClick={e => e.stopPropagation()} style={{ maxWidth: 500 }}>
            <div className="tv-modal-header otp-modal-head">
              <h3 className="tv-modal-title">Подтверждение удаления</h3>
              <ModalCloseButton isMobileShell={isMobileShell} onClick={closeDeleteModal} disabled={!!actionLoadingKey} />
            </div>
            <form onSubmit={submitDeleteTask}>
              <div className="tv-modal-body">
                <div style={{
                  background: '#fff1f2',
                  border: '1px solid #fecdd3',
                  borderRadius: 'var(--radius-sm)',
                  padding: '11px 13px',
                  fontSize: 13,
                  color: '#9f1239',
                  marginBottom: 12,
                }}>
                  Задача будет удалена без возможности восстановления.
                </div>
                <div style={{ fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.55 }}>
                  Удалить задачу:
                </div>
                <div style={{
                  marginTop: 8,
                  background: '#f8f7f4',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '10px 14px',
                  fontSize: 13,
                  color: 'var(--ink)',
                  wordBreak: 'break-word'
                }}>
                  {deleteModal.taskSubject || `#${deleteModal.taskId}`}
                </div>
              </div>
              <div className="tv-modal-footer">
                <button
                  type="button"
                  className="tv-btn tv-btn-ghost tv-modal-cancel"
                  disabled={!!actionLoadingKey}
                  onClick={closeDeleteModal}
                >
                  Отмена
                </button>
                <button type="submit" className="tv-btn tv-btn-rose" disabled={!!actionLoadingKey}>
                  {actionLoadingKey === deleteModalLoadingKey ? 'Удаляю...' : 'Удалить задачу'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Complete Modal */}
      {completeModal.open && (
        <div className="tv-modal-overlay otp-modal-root otp-modal-dim" {...completeOverlayProps}>
          <div className="tv-modal otp-modal-panel" onClick={e => e.stopPropagation()}>
            <div className="tv-modal-header otp-modal-head">
              <h3 className="tv-modal-title">Завершение задачи</h3>
              <ModalCloseButton isMobileShell={isMobileShell} onClick={closeCompleteModal} />
            </div>
            <form onSubmit={submitComplete}>
              <div className="tv-modal-body">
                {completeModal.taskSubject && (
                  <div style={{
                    background: '#f8f7f4', border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)', padding: '10px 14px',
                    fontSize: 13, color: 'var(--ink-2)', marginBottom: 16,
                    display: 'flex', gap: 8, alignItems: 'flex-start',
                  }}>
                    <span style={{ color: 'var(--ink-3)', marginTop: 1 }}>📌</span>
                    <span><strong style={{ color: 'var(--ink)' }}>{completeModal.taskSubject}</strong></span>
                  </div>
                )}
                <div className="tv-form-grid">
                  <div className="tv-form-field">
                    <label>Отчёт о проделанной работе *</label>
                    <textarea className="tv-textarea" value={completionSummary}
                      placeholder="Что сделано, к какому результату пришли, что осталось"
                      style={{ minHeight: 110 }}
                      autoFocus
                      disabled={!!actionLoadingKey}
                      onChange={e => setCompletionSummary(e.target.value)} />
                  </div>
                  <div className="tv-form-field">
                    <label>Затрачено времени</label>
                    <input className="tv-input" value={completionSpent}
                      placeholder="например 3ч, 90, 1д 2ч"
                      disabled={!!actionLoadingKey}
                      onChange={e => setCompletionSpent(e.target.value)} />
                    <p className="tv-field-hint">
                      {(() => {
                        const spent = parseSpentInput(completionSpent);
                        const estimate = Number(completeModal.estimateMinutes || 0);
                        if (!spent && estimate) return `Оценка по задаче: ${formatSpentMinutes(estimate)}`;
                        if (!spent) return 'Необязательно, но так видно реальные трудозатраты в таймлайне.';
                        if (!estimate) return formatSpentMinutes(spent);
                        const delta = spent - estimate;
                        if (delta === 0) return `${formatSpentMinutes(spent)} — ровно по оценке`;
                        return delta > 0
                          ? `${formatSpentMinutes(spent)} — на ${formatSpentMinutes(delta)} больше оценки`
                          : `${formatSpentMinutes(spent)} — на ${formatSpentMinutes(-delta)} меньше оценки`;
                      })()}
                    </p>
                  </div>
                  {/* Та же панель файлов, что в форме задачи: системное поле выбора
                      файла выглядело деталью из другой программы. */}
                  <div className="tv-form-field">
                    <label>Итоговые файлы</label>
                    <TaskFileDropzone
                      files={completionFiles}
                      disabled={!!actionLoadingKey}
                      onChange={setCompletionFiles}
                      title="Итоговые файлы"
                    />
                  </div>
                </div>
              </div>
              <div className="tv-modal-footer">
                <button type="button" className="tv-btn tv-btn-ghost tv-modal-cancel" disabled={!!actionLoadingKey}
                  onClick={closeCompleteModal}>Отмена</button>
                <button type="submit" className="tv-btn tv-btn-indigo" disabled={!!actionLoadingKey}>
                  {actionLoadingKey === `${completeModal.taskId}:completed` ? 'Сохраняю...' : 'Отметить выполненной'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Status Modal */}
      {statusModal.open && (
        <div className="tv-modal-overlay otp-modal-root otp-modal-dim" {...statusOverlayProps}>
          <div className="tv-modal otp-modal-panel" onClick={e => e.stopPropagation()}>
            <div className="tv-modal-header otp-modal-head">
              <h3 className="tv-modal-title">{statusModalTitle}</h3>
              <ModalCloseButton isMobileShell={isMobileShell} onClick={closeStatusModal} />
            </div>
            <form onSubmit={submitStatusModal}>
              <div className="tv-modal-body">
                {statusModal.taskSubject && (
                  <div style={{
                    background: '#f8f7f4', border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)', padding: '10px 14px',
                    fontSize: 13, color: 'var(--ink-2)', marginBottom: 16,
                    display: 'flex', gap: 8, alignItems: 'flex-start',
                  }}>
                    <span style={{ color: 'var(--ink-3)', marginTop: 1 }}>📌</span>
                    <span><strong style={{ color: 'var(--ink)' }}>{statusModal.taskSubject}</strong></span>
                  </div>
                )}
                <div className="tv-form-grid">
                  <div className="tv-form-field">
                    <label>{statusModalCommentLabel}</label>
                    <textarea className="tv-textarea" value={statusComment}
                      placeholder="Добавьте пояснение (необязательно)"
                      style={{ minHeight: 110 }}
                      autoFocus
                      disabled={!!actionLoadingKey}
                      onChange={e => setStatusComment(e.target.value)} />
                  </div>
                  <div className="tv-form-field">
                    <label>Прикрепить файлы</label>
                    <TaskFileDropzone
                      files={statusFiles}
                      disabled={!!actionLoadingKey}
                      onChange={setStatusFiles}
                      title="Файлы к комментарию"
                    />
                  </div>
                </div>
              </div>
              <div className="tv-modal-footer">
                <button type="button" className="tv-btn tv-btn-ghost tv-modal-cancel" disabled={!!actionLoadingKey}
                  onClick={closeStatusModal}>Отмена</button>
                <button type="submit" className={`tv-btn ${isReturnAction ? 'tv-btn-rose' : 'tv-btn-indigo'}`} disabled={!!actionLoadingKey}>
                  {actionLoadingKey === statusModalLoadingKey ? 'Сохраняю...' : statusModalSubmitLabel}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

const areEqual = (prev, next) =>
  prev.user === next.user &&
  prev.apiBaseUrl === next.apiBaseUrl &&
  prev.withAccessTokenHeader === next.withAccessTokenHeader &&
  prev.pinnedTaskId === next.pinnedTaskId &&
  prev.focusTaskRequest === next.focusTaskRequest &&
  prev.externalRefreshToken === next.externalRefreshToken &&
  prev.onOpenProduction === next.onOpenProduction;

export default React.memo(TasksView, areEqual);
