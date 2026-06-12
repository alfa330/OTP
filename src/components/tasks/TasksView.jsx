import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import {
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  ChevronLeft as LucideChevronLeft,
  GripHorizontal,
  Link2,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Palette,
  PanelRightOpen,
  PictureInPicture2,
  Pin,
  PinOff,
  RefreshCw,
  SlidersHorizontal,
  StickyNote,
} from 'lucide-react';
import { normalizeRole, isAdminLikeRole, isSupervisorRole } from '../../utils/roles';
import FaIcon from '../common/FaIcon';

/* ─── Google Fonts ─── */
const fontLink = document.createElement('link');
fontLink.rel = 'stylesheet';
fontLink.href = 'https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;1,9..40,300&display=swap';
document.head.appendChild(fontLink);

/* ─── Styles ─── */
const styleTag = document.createElement('style');
styleTag.textContent = `
  .tv-root * { font-family: 'DM Sans', sans-serif; box-sizing: border-box; }
  .tv-root h1, .tv-root h2, .tv-root h3, .tv-root .heading { font-family: 'Syne', sans-serif; }

  .tv-root {
    --bg: #f4f3f0;
    --surface: #ffffff;
    --surface-2: #fafaf8;
    --border: #e8e5df;
    --border-strong: #ccc9c0;
    --ink: #1a1916;
    --ink-2: #5c5852;
    --ink-3: #9e9a93;
    --accent: #1a1916;
    --accent-fg: #ffffff;
    --amber: #d97706;
    --indigo: #4338ca;
    --emerald: #059669;
    --rose: #e11d48;
    --blue: #2563eb;
    --shadow-sm: 0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
    --shadow-md: 0 4px 16px rgba(0,0,0,.08), 0 2px 6px rgba(0,0,0,.05);
    --shadow-lg: 0 20px 60px rgba(0,0,0,.14), 0 8px 24px rgba(0,0,0,.08);
    --radius: 14px;
    --radius-sm: 9px;
    --radius-xs: 6px;
    background: var(--bg);
    min-height: 100vh;
    padding: 28px 24px;
  }

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
    font-family: 'Syne', sans-serif;
    font-size: 22px;
    font-weight: 700;
    color: var(--ink);
    margin: 0;
  }
  .tv-topbar-actions { display: flex; align-items: center; gap: 8px; }

  /* ── Stats strip ── */
  .tv-stats-strip {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 28px;
  }
  .tv-stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
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
    font-family: 'Syne', sans-serif;
    font-size: 26px;
    font-weight: 700;
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
    font-family: 'Syne', sans-serif;
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
    font-family: 'DM Sans', sans-serif;
  }
  .tv-search-input:focus {
    border-color: var(--ink-3);
    box-shadow: 0 0 0 3px rgba(26,25,22,.06);
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
  .tv-chip.is-active {
    background: var(--ink);
    border-color: var(--ink);
    color: #fff;
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
    font-size: 13px; font-weight: 500;
    border: 1px solid transparent;
    cursor: pointer; transition: all .15s ease;
    white-space: nowrap; background: none;
  }
  .tv-btn:disabled { opacity: .5; cursor: not-allowed; }
  .tv-btn-ghost {
    background: transparent;
    border-color: var(--border-strong);
    color: var(--ink-2);
  }
  .tv-btn-ghost:hover:not(:disabled) {
    background: var(--surface); color: var(--ink); border-color: var(--ink-3);
  }
  .tv-btn-primary { background: var(--accent); color: var(--accent-fg); border-color: var(--accent); }
  .tv-btn-primary:hover:not(:disabled) { background: #333; }
  .tv-btn-amber   { background: #fef3c7; color: var(--amber); border-color: #fde68a; }
  .tv-btn-amber:hover:not(:disabled)   { background: #fde68a; }
  .tv-btn-indigo  { background: #eef2ff; color: var(--indigo); border-color: #c7d2fe; }
  .tv-btn-indigo:hover:not(:disabled)  { background: #e0e7ff; }
  .tv-btn-emerald { background: #d1fae5; color: var(--emerald); border-color: #a7f3d0; }
  .tv-btn-emerald:hover:not(:disabled) { background: #a7f3d0; }
  .tv-btn-rose    { background: #ffe4e6; color: var(--rose); border-color: #fecdd3; }
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
  .tv-task-row.is-urgent {
    border-color: #fcd34d;
    background: #fffdf6;
  }
  .tv-task-row.is-urgent:hover { border-color: #f59e0b; }
  .tv-task-row.is-critical {
    border-color: #fecdd3;
    background: #fff6f7;
  }
  .tv-task-row.is-critical:hover { border-color: #fb7185; }
  .tv-task-row-indicator {
    width: 3px; height: 28px; border-radius: 99px; flex-shrink: 0;
  }
  .tv-task-row.is-urgent .tv-task-row-indicator,
  .tv-task-row.is-critical .tv-task-row-indicator {
    width: 4px; height: 32px;
  }
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
    background: #e8e5df;
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
    background: #f1f0ed;
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
    background: #e8e5df;
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
  .tv-badge {
    display: inline-flex; align-items: center;
    padding: 1px 7px; border-radius: 99px;
    font-size: 11px; font-weight: 500;
    border: 1px solid transparent;
    white-space: nowrap; line-height: 1.25;
  }
  .tv-badge-gray    { background: #f1f0ed; color: var(--ink-2);  border-color: var(--border); }
  .tv-badge-blue    { background: #dbeafe; color: #1e40af;       border-color: #bfdbfe; }
  .tv-badge-amber   { background: #fef3c7; color: #92400e;       border-color: #fde68a; }
  .tv-badge-indigo  { background: #eef2ff; color: #3730a3;       border-color: #c7d2fe; }
  .tv-badge-emerald { background: #d1fae5; color: #065f46;       border-color: #a7f3d0; }
  .tv-badge-rose    { background: #ffe4e6; color: #9f1239;       border-color: #fecdd3; }
  .tv-badge-teal    { background: #ccfbf1; color: #0f766e;       border-color: #99f6e4; }
  .tv-badge-violet  { background: #ede9fe; color: #5b21b6;       border-color: #ddd6fe; }

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

  /* ── Drawer ── */
  .tv-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.3);
    backdrop-filter: blur(2px); z-index: 40;
    animation: tvFadeIn .2s ease;
  }
  @keyframes tvFadeIn { from { opacity: 0 } to { opacity: 1 } }

  .tv-drawer {
    position: fixed; top: 0; right: 0; bottom: 0;
    width: min(560px, 100vw);
    background: var(--surface); z-index: 50;
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
    font-family: 'Syne', sans-serif;
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
  .tv-drawer-footer {
    padding: 14px 20px; border-top: 1px solid var(--border);
    display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end;
    flex-shrink: 0; background: var(--bg);
  }

  /* ── Detail blocks ── */
  .tv-info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .tv-info-item label {
    display: block; font-size: 10.5px; font-weight: 600;
    letter-spacing: .06em; text-transform: uppercase;
    color: var(--ink-3); margin-bottom: 3px;
  }
  .tv-info-item span { font-size: 13px; color: var(--ink); }
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
    background: #f1f0ed; border: 1px solid var(--border);
    color: var(--ink-2); font-size: 12px;
    cursor: pointer; transition: all .12s;
    max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .tv-file-btn:hover { background: var(--border); color: var(--ink); }

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
  .tv-note-topic-btn.is-active { box-shadow: 0 0 0 3px rgba(26,25,22,.06); }
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
  .tv-form-inline-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
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
    background: #f4f3f0;
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
    display: flex; gap: 16px;
  }
  .tv-participant {
    display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0;
  }
  .tv-participant-avatar {
    width: 32px; height: 32px; border-radius: 50%;
    background: #e8e5df;
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; font-weight: 700; color: var(--ink-2);
    flex-shrink: 0; text-transform: uppercase;
  }
  .tv-participant-info { min-width: 0; }
  .tv-participant-role { font-size: 10px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
  .tv-participant-name { font-size: 13px; font-weight: 500; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* ── Modal ── */
  .tv-modal-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.35);
    backdrop-filter: blur(3px); z-index: 60;
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
    font-family: 'Syne', sans-serif;
    font-size: 15px; font-weight: 600; color: var(--ink); margin: 0;
  }
  .tv-modal-body { padding: 20px 22px; overflow-y: auto; flex: 1; }
  .tv-modal-footer {
    padding: 13px 22px; border-top: 1px solid var(--border);
    display: flex; justify-content: flex-end; gap: 8px;
    background: var(--bg); flex-shrink: 0;
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
    font-family: 'DM Sans', sans-serif;
  }
  .tv-input:focus, .tv-textarea:focus, .tv-select:focus {
    border-color: var(--ink-3); box-shadow: 0 0 0 3px rgba(26,25,22,.06);
  }
  .tv-input:disabled, .tv-textarea:disabled, .tv-select:disabled { opacity: .55; cursor: not-allowed; }
  .tv-textarea { resize: vertical; min-height: 86px; line-height: 1.5; }

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
    background: #f1f0ed; border: 1px solid var(--border);
    font-size: 10px; color: var(--ink-3);
    font-family: monospace;
    line-height: 1.5;
  }

  /* ── Pinned task widget ── */
  .tv-pin-widget {
    --bg: #f4f3f0;
    --surface: #ffffff;
    --surface-2: #fafaf8;
    --border: #e8e5df;
    --border-strong: #ccc9c0;
    --ink: #1a1916;
    --ink-2: #5c5852;
    --ink-3: #9e9a93;
    --accent: #1a1916;
    --accent-fg: #ffffff;
    --amber: #d97706;
    --indigo: #4338ca;
    --emerald: #059669;
    --rose: #e11d48;
    --blue: #2563eb;
    --shadow-lg: 0 20px 60px rgba(0,0,0,.16), 0 8px 24px rgba(0,0,0,.1);
    position: fixed;
    z-index: 75;
    width: min(360px, calc(100vw - 24px));
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: 12px;
    box-shadow: var(--shadow-lg);
    overflow: hidden;
    color: var(--ink);
    font-family: 'DM Sans', sans-serif;
    user-select: none;
  }
  .tv-pin-widget.is-detached {
    position: relative;
    inset: auto;
    width: 100%;
    height: 100vh;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    border-radius: 0;
    border: 0;
    box-shadow: none;
  }
  .tv-pin-widget.is-dragging {
    box-shadow: 0 24px 72px rgba(0,0,0,.2), 0 10px 28px rgba(0,0,0,.14);
  }
  .tv-pin-header {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 12px;
    background: var(--surface-2);
    border-bottom: 1px solid var(--border);
  }
  .tv-pin-drag-handle {
    width: 24px;
    min-height: 32px;
    border: 0;
    background: transparent;
    color: var(--ink-3);
    cursor: grab;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    touch-action: none;
    flex-shrink: 0;
  }
  .tv-pin-drag-handle:active {
    cursor: grabbing;
  }
  .tv-pin-heading {
    flex: 1;
    min-width: 0;
  }
  .tv-pin-kicker {
    display: block;
    margin-bottom: 2px;
    color: var(--ink-3);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
  }
  .tv-pin-title {
    margin: 0;
    font-family: 'Syne', sans-serif;
    font-size: 14px;
    line-height: 1.35;
    font-weight: 700;
    color: var(--ink);
    word-break: break-word;
  }
  .tv-pin-header-actions {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }
  .tv-pin-header-btn {
    width: 30px;
    height: 30px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--ink-2);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all .15s ease;
  }
  .tv-pin-header-btn:hover:not(:disabled) {
    background: var(--border-strong);
    color: var(--ink);
    border-color: var(--border-strong);
  }
  .tv-pin-header-btn.is-active {
    background: var(--accent);
    color: var(--accent-fg);
    border-color: var(--accent);
  }
  .tv-pin-header-btn-danger {
    color: #e11d48;
  }
  .tv-pin-header-btn-danger:hover:not(:disabled) {
    background: #fff1f2;
    border-color: var(--border);
    color: #be123c;
  }
  .tv-pin-header-btn:disabled {
    opacity: .55;
    cursor: not-allowed;
  }
  .tv-pin-body {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
  }
  .tv-pin-widget.is-detached .tv-pin-header {
    flex-shrink: 0;
  }
  .tv-pin-widget.is-detached .tv-pin-body {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }
  .tv-pin-widget.is-detached .tv-pin-body:not(.is-menu-open) {
    overflow-y: auto;
  }
  .tv-pin-widget.is-detached .tv-pin-body.is-notes-open,
  .tv-pin-widget.is-detached .tv-pin-body.is-task-form-open {
    overflow: hidden;
  }
  .tv-pin-body.is-notes-open {
    max-height: min(660px, calc(100vh - 88px));
    min-height: 0;
    overflow-y: auto;
  }
  .tv-pin-widget.is-detached .tv-pin-body.is-notes-open {
    max-height: none;
    overflow: hidden;
  }
  .tv-pin-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }
  .tv-pin-summary {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .tv-pin-files {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .tv-pin-note-panel {
    display: flex;
    flex-direction: column;
    gap: 7px;
    padding: 10px;
    border-radius: 10px;
    background: var(--bg);
    border: 1px solid var(--border);
  }
  .tv-pin-note-panel textarea {
    width: 100%;
    min-height: 104px;
    resize: vertical;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    color: var(--ink);
    font-size: 12px;
    line-height: 1.45;
    padding: 8px;
    outline: none;
    user-select: text;
  }
  .tv-pin-palette-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .tv-pin-palette-btn {
    width: 30px;
    height: 24px;
    border-radius: 8px;
    border: 2px solid var(--border);
    cursor: pointer;
    display: inline-flex;
    overflow: hidden;
    padding: 0;
    background: var(--surface);
  }
  .tv-pin-palette-btn.is-active { border-color: var(--accent); }
  .tv-pin-palette-swatch { flex: 1; }
  .tv-pin-mini-checklist {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .tv-pin-mini-checklist .tv-checklist-row {
    padding: 7px 8px;
  }
  .tv-pin-note-footer,
  .tv-pin-task-form-actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }
  .tv-pin-task-form {
    display: flex;
    flex-direction: column;
    gap: 9px;
    padding: 10px;
    border-radius: 10px;
    background: var(--surface-2);
    border: 1px solid var(--border);
  }
  .tv-pin-task-form.is-fullscreen {
    flex: 1;
    min-height: 0;
    height: 100%;
    overflow-y: auto;
    padding: 12px;
    border-radius: 0;
    border: 0;
    background: var(--surface);
  }
  .tv-pin-task-form.is-fullscreen .tv-pin-task-form-title {
    padding: 2px 0 4px;
    border-bottom: 1px solid var(--border);
  }
  .tv-pin-task-form.is-fullscreen .tv-form-field,
  .tv-pin-task-form.is-fullscreen .tv-soft-block {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 9px;
  }
  .tv-pin-task-form.is-fullscreen .tv-soft-block .tv-form-field {
    background: transparent;
    border: 0;
    padding: 0;
  }
  .tv-pin-task-form.is-fullscreen .tv-pin-task-form-actions {
    position: sticky;
    bottom: 0;
    margin-top: auto;
    padding-top: 8px;
    background: var(--surface);
    border-top: 1px solid var(--border);
  }
  .tv-pin-task-form-title {
    margin: 0;
    color: var(--ink);
    font-size: 13px;
    font-weight: 800;
  }
  .tv-pin-task-form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .tv-pin-task-form .tv-textarea {
    min-height: 64px;
  }
  .tv-pin-task-form .tv-form-field {
    margin: 0;
  }
  .tv-pin-task-form .tv-form-field label {
    font-size: 10.5px;
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
  .tv-pin-form-error {
    color: var(--rose);
    font-size: 12px;
  }
  .tv-pin-file-section .tv-block-label {
    margin: 0 0 6px;
  }
  .tv-pin-file-section-result .tv-file-btn {
    background: #e0e7ff;
    border-color: #c7d2fe;
    color: #3730a3;
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
  .tv-pin-task-form.is-fullscreen .tv-file-dropzone {
    min-height: 76px;
    padding: 12px;
  }
  .tv-pin-menu-trigger {
    width: 30px;
    height: 32px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--ink-2);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all .15s ease;
  }
  .tv-pin-menu-trigger:hover:not(:disabled) {
    background: var(--border-strong);
    color: var(--ink);
    border-color: var(--border-strong);
  }
  .tv-pin-menu-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .tv-pin-menu-title {
    margin: 0;
    color: var(--ink-2);
    font-size: 12px;
    font-weight: 700;
    flex: 1;
    min-width: 0;
  }
  .tv-pin-menu-head-actions {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }
  .tv-pin-menu-create-btn {
    padding: 7px 10px;
    font-size: 12px;
  }
  .tv-pin-task-menu-shell {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-height: 0;
  }
  .tv-pin-widget.is-detached .tv-pin-task-menu-shell {
    flex: 1;
    min-height: 0;
  }
  .tv-pin-menu-tabs {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    padding: 3px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--bg);
  }
  .tv-pin-menu-tab {
    border: 0;
    border-radius: 8px;
    padding: 6px 8px;
    background: transparent;
    color: var(--ink-2);
    font-size: 11.5px;
    font-weight: 800;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    cursor: pointer;
    transition: all .15s ease;
  }
  .tv-pin-menu-tab:hover:not(:disabled) {
    color: var(--ink);
  }
  .tv-pin-menu-tab.is-active {
    background: var(--surface);
    color: var(--ink);
    box-shadow: var(--shadow-sm);
  }
  .tv-pin-menu-tab-count {
    min-width: 18px;
    height: 17px;
    padding: 0 5px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--ink-2);
    font-size: 10px;
    line-height: 1;
  }
  .tv-pin-menu-tab.is-active .tv-pin-menu-tab-count {
    background: #eef2ff;
    border-color: #c7d2fe;
    color: #3730a3;
  }
  .tv-pin-task-menu {
    display: grid;
    grid-template-columns: 48px minmax(0, 1fr);
    gap: 10px;
    min-height: 176px;
  }
  .tv-pin-widget.is-detached .tv-pin-task-menu {
    flex: 1;
    min-height: 0;
  }
  .tv-pin-people-rail {
    display: flex;
    flex-direction: column;
    gap: 6px;
    align-items: center;
  }
  .tv-pin-widget.is-detached .tv-pin-people-rail {
    min-height: 0;
    overflow-y: auto;
    gap: 10px;
    padding-bottom: 4px;
  }
  .tv-pin-person-btn {
    width: 42px;
    height: 42px;
    position: relative;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--surface);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all .15s ease;
  }
  .tv-pin-person-btn:hover,
  .tv-pin-person-btn.is-active {
    border-color: var(--accent);
    background: var(--surface-2);
    color: var(--ink);
  }
  .tv-pin-person-btn .tv-avatar-md {
    width: 30px;
    height: 30px;
    font-size: 11px;
  }
  .tv-pin-person-count {
    display: none;
    position: absolute;
    right: -4px;
    bottom: -5px;
    min-width: 18px;
    height: 18px;
    padding: 0 5px;
    border-radius: 999px;
    border: 2px solid var(--surface);
    font-size: 10px;
    line-height: 14px;
    font-weight: 700;
    text-align: center;
    color: #ffffff;
  }
  .tv-pin-widget.is-detached .tv-pin-person-count {
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .tv-pin-person-count.is-alert {
    background: #f59e0b;
  }
  .tv-pin-person-count.is-active {
    background: #2563eb;
  }
  .tv-pin-menu-panel {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .tv-pin-widget.is-detached .tv-pin-menu-panel {
    min-height: 0;
  }
  .tv-pin-person-summary {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 10px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--bg);
  }
  .tv-pin-person-summary-name {
    color: var(--ink);
    font-size: 13px;
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tv-pin-person-summary-stats {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    color: var(--ink-2);
    font-size: 11px;
  }
  .tv-pin-person-summary-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .tv-pin-person-task-list {
    display: flex;
    flex-direction: column;
    gap: 5px;
    max-height: 180px;
    overflow: auto;
  }
  .tv-pin-widget.is-detached .tv-pin-person-task-list {
    flex: 1;
    min-height: 0;
    max-height: none;
    overflow-y: auto;
  }
  .tv-pin-person-task {
    width: 100%;
    border: 1px solid var(--border);
    border-radius: 9px;
    background: var(--surface);
    color: var(--ink);
    padding: 8px 9px;
    text-align: left;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .tv-pin-person-task:hover,
  .tv-pin-person-task.is-active {
    border-color: var(--accent);
    background: var(--surface-2);
    color: var(--ink);
  }
  .tv-pin-person-task-title {
    font-size: 12px;
    font-weight: 600;
    line-height: 1.35;
  }
  .tv-pin-person-task-meta {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
  }
  .tv-pin-description {
    margin: 0;
    color: var(--ink-2);
    font-size: 12.5px;
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 150px;
    overflow: auto;
    user-select: text;
  }
  .tv-pin-meta {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .tv-pin-meta-item {
    min-width: 0;
    padding: 8px 9px;
    border-radius: 8px;
    background: var(--bg);
    border: 1px solid var(--border);
  }
  .tv-pin-meta-label {
    display: block;
    margin-bottom: 2px;
    color: var(--ink-3);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .05em;
    text-transform: uppercase;
  }
  .tv-pin-meta-value {
    display: block;
    color: var(--ink);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tv-pin-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
  }
  .tv-pin-actions .tv-btn {
    justify-content: center;
    flex: 1 1 140px;
    min-height: 34px;
    white-space: normal;
    line-height: 1.25;
  }
  .tv-pin-empty-actions {
    color: var(--ink-3);
    font-size: 12px;
  }

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
    .tv-pin-widget {
      width: min(360px, calc(100vw - 16px));
    }
    .tv-checklist-editor:not(.is-compact) .tv-checklist-editor-row,
    .tv-pin-task-form-grid {
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
    .tv-pin-meta {
      grid-template-columns: 1fr;
    }
  }
  @media (max-width: 400px) {
    .tv-stats-strip { grid-template-columns: 1fr 1fr; gap: 8px; }
    .tv-stat-value { font-size: 22px; }
    .tv-topbar-title { font-size: 18px; }
  }
`;
document.head.appendChild(styleTag);

const TASK_VIEW_QUERY_PARAM = 'view';
const TASK_ID_QUERY_PARAM = 'task_id';

const buildTaskDeepLink = (taskId) => {
  if (typeof window === 'undefined') return '';
  const normalizedTaskId = Number(taskId || 0);
  if (!Number.isInteger(normalizedTaskId) || normalizedTaskId <= 0) return '';
  try {
    const url = new URL(window.location.href);
    url.searchParams.set(TASK_VIEW_QUERY_PARAM, 'tasks');
    url.searchParams.set(TASK_ID_QUERY_PARAM, String(normalizedTaskId));
    return url.toString();
  } catch (error) {
    return '';
  }
};

const syncTaskDeepLink = (taskId) => {
  if (typeof window === 'undefined') return;
  try {
    const url = new URL(window.location.href);
    if (taskId) {
      url.searchParams.set(TASK_VIEW_QUERY_PARAM, 'tasks');
      url.searchParams.set(TASK_ID_QUERY_PARAM, String(taskId));
    } else {
      url.searchParams.delete(TASK_ID_QUERY_PARAM);
    }
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  } catch (error) {
    // Ignore URL-sync failures in constrained browser contexts.
  }
};

/* ─── Constants ─── */
const TAG_OPTIONS = [
  { value: 'task',       label: 'Задача' },
  { value: 'problem',    label: 'Проблема' },
  { value: 'suggestion', label: 'Предложение' },
];

const TAG_META = {
  task:       { label: 'Задача',      badge: 'tv-badge-blue' },
  problem:    { label: 'Проблема',    badge: 'tv-badge-rose' },
  suggestion: { label: 'Предложение', badge: 'tv-badge-teal' },
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

const PIN_PALETTES = [
  {
    id: 'paper',
    label: 'Светлая',
    vars: {
      '--bg': '#f4f3f0',
      '--surface': '#ffffff',
      '--surface-2': '#fafaf8',
      '--border': '#e8e5df',
      '--border-strong': '#ccc9c0',
      '--ink': '#1a1916',
      '--ink-2': '#5c5852',
      '--ink-3': '#9e9a93',
      '--accent': '#1a1916',
      '--accent-fg': '#ffffff',
    }
  },
  {
    id: 'midnight',
    label: 'Тёмная',
    vars: {
      '--bg': '#101318',
      '--surface': '#171b22',
      '--surface-2': '#1f2530',
      '--border': '#303846',
      '--border-strong': '#485365',
      '--ink': '#f8fafc',
      '--ink-2': '#cbd5e1',
      '--ink-3': '#94a3b8',
      '--accent': '#93c5fd',
      '--accent-fg': '#0f172a',
    }
  },
  {
    id: 'mint',
    label: 'Мята',
    vars: {
      '--bg': '#ecfdf5',
      '--surface': '#ffffff',
      '--surface-2': '#d1fae5',
      '--border': '#a7f3d0',
      '--border-strong': '#34d399',
      '--ink': '#064e3b',
      '--ink-2': '#047857',
      '--ink-3': '#10b981',
      '--accent': '#047857',
      '--accent-fg': '#ffffff',
    }
  },
  {
    id: 'focus',
    label: 'Контраст',
    vars: {
      '--bg': '#fff7ed',
      '--surface': '#fffbeb',
      '--surface-2': '#fed7aa',
      '--border': '#fdba74',
      '--border-strong': '#fb923c',
      '--ink': '#431407',
      '--ink-2': '#9a3412',
      '--ink-3': '#c2410c',
      '--accent': '#ea580c',
      '--accent-fg': '#ffffff',
    }
  },
];

const PIN_PALETTE_STORAGE_KEY = 'otp:pinned-task-palette';

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

const ROLE_LABELS = { admin: 'Админ', sv: 'СВ' };
const TASKS_PAGE_SIZE = 20;

const buildTaskActionButtons = (task, currentUserId, currentUserRole) => {
  const assigneeId = Number(task?.assignee?.id || 0);
  const creatorId  = Number(task?.creator?.id  || 0);
  const isAssignee = assigneeId === currentUserId;
  const isCreator  = creatorId === currentUserId;
  const isSuperAdmin = normalizeRole(currentUserRole) === 'super_admin';
  const canReview  = !isAssignee && (isAdminLikeRole(currentUserRole) || creatorId === currentUserId || isSupervisorRole(currentUserRole));
  const s = task?.status;
  const btns = [];
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

const splitDeadlineMinutes = (totalMinutes) => {
  const total = Math.max(0, Number(totalMinutes || 0));
  const days = Math.floor(total / (24 * 60));
  const remainder = total % (24 * 60);
  const hours = Math.floor(remainder / 60);
  const minutes = remainder % 60;
  return { days: String(days || ''), hours: String(hours || ''), minutes: String(minutes || '') };
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
      return {
        title,
        is_required: typeof item === 'object' ? item?.is_required !== false : true,
      };
    })
    .filter(Boolean);
};

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
  };
};

const EMPTY_TASK_FORM = {
  subject: '',
  description: '',
  tag: 'task',
  priority: 'normal',
  assignedTo: '',
  deadlineDays: '',
  deadlineHours: '',
  deadlineMinutes: '',
  isRegulation: false,
  recurrenceType: '',
  recurrenceInterval: '1',
  checklistItems: [],
};

const buildEmptyTaskForm = (overrides = {}) => ({
  ...EMPTY_TASK_FORM,
  checklistItems: [createEmptyChecklistDraftItem()],
  ...overrides,
});

const taskToTaskForm = (task, fallbackAssignedTo = '') => {
  const deadline = splitDeadlineMinutes(task?.deadline_duration_minutes);
  return buildEmptyTaskForm({
    subject: task?.subject || '',
    description: task?.description || '',
    tag: task?.tag || 'task',
    priority: task?.priority || 'normal',
    assignedTo: String(task?.assignee?.id || fallbackAssignedTo || ''),
    deadlineDays: deadline.days,
    deadlineHours: deadline.hours,
    deadlineMinutes: deadline.minutes,
    isRegulation: Boolean(task?.is_regulation || task?.recurrence_type),
    recurrenceType: task?.recurrence_type || '',
    recurrenceInterval: String(task?.recurrence_interval || '1'),
    checklistItems: checklistToFormItems(task?.checklist),
  });
};

const formChecklistItems = (items) => normalizeChecklistItems(items);

const numberFieldValue = (value) => {
  const num = Number(value);
  return Number.isFinite(num) && num > 0 ? String(Math.floor(num)) : '0';
};

const buildTaskJsonPayload = (values) => ({
  subject: String(values.subject || '').trim(),
  description: String(values.description || '').trim(),
  tag: values.tag || 'task',
  priority: values.priority || 'normal',
  assigned_to: Number(values.assignedTo || 0),
  deadline_days: numberFieldValue(values.deadlineDays),
  deadline_hours: numberFieldValue(values.deadlineHours),
  deadline_minutes: numberFieldValue(values.deadlineMinutes),
  is_regulation: Boolean(values.isRegulation),
  recurrence_type: values.isRegulation ? (values.recurrenceType || 'daily') : '',
  recurrence_interval: values.isRegulation ? numberFieldValue(values.recurrenceInterval || '1') : '1',
  checklist_items: formChecklistItems(values.checklistItems),
});

const appendTaskFormData = (body, values) => {
  const payload = buildTaskJsonPayload(values);
  body.append('subject', payload.subject);
  body.append('description', payload.description);
  body.append('tag', payload.tag);
  body.append('priority', payload.priority);
  body.append('assigned_to', String(payload.assigned_to || ''));
  body.append('deadline_days', payload.deadline_days);
  body.append('deadline_hours', payload.deadline_hours);
  body.append('deadline_minutes', payload.deadline_minutes);
  body.append('is_regulation', payload.is_regulation ? '1' : '0');
  body.append('recurrence_type', payload.recurrence_type);
  body.append('recurrence_interval', payload.recurrence_interval);
  body.append('checklist_items', JSON.stringify(payload.checklist_items));
};

const filesFromList = (files) => Array.from(files || []).filter(Boolean);

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

const groupTasksByPerson = (list, getPerson) => {
  const groups = new Map();
  list.forEach((task) => {
    const person = getPerson(task) || {};
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
      <span className="tv-note-topic-date">{dueLabel || (note?.saved_at ? fmtShortDateTime(note.saved_at) : 'Черновик')}</span>
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
        <span className="tv-pin-empty-actions">{emptyText}</span>
      )}
    </div>
  </section>
));

const TaskNotesPanel = React.memo(({ notesState, compact = false, fullScreen = false }) => {
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
    if (draftPriority !== 'normal') parts.push(draftPriorityMeta.label.toLowerCase());
    if (normalizedDraft.is_task) parts.push(normalizedDraft.is_done ? 'выполнено' : 'как задача');
    return parts.length ? parts.join(' · ') : 'не заданы';
  }, [draftPriority, draftPriorityMeta.label, normalizedDraft.due_at, normalizedDraft.is_done, normalizedDraft.is_task]);

  const updateDraft = useCallback((patch) => {
    setDraft((prev) => normalizeTaskNote({ ...normalizeTaskNote(prev), ...patch }));
  }, [setDraft]);

  return (
    <div className={`tv-notes-panel ${compact ? 'is-compact' : ''} ${fullScreen ? 'is-fullscreen' : ''}`}>
      <div className="tv-notes-toolbar">
        <div>
          <span className="tv-block-label" style={{ margin: 0 }}>Заметки</span>
          <p>{backendReady ? 'Сохраняются в базе данных' : 'Сервер недоступен, включён локальный режим'}</p>
        </div>
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
            <label className="tv-form-field">
              <span>Дедлайн</span>
              <input
                className="tv-input"
                type="datetime-local"
                value={draftDueValue}
                disabled={isSaving}
                onChange={(event) => updateDraft({ due_at: event.target.value || null })}
              />
            </label>
            <label className="tv-form-field">
              <span>Приоритет</span>
              <select
                className="tv-select"
                value={normalizedDraft.priority || 'normal'}
                disabled={isSaving}
                onChange={(event) => updateDraft({ priority: event.target.value })}
              >
                {PRIORITY_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </label>
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

const TaskRow = React.memo(({ task, onClick, onPin, isPinned }) => {
  const sm = STATUS_META[task.status] || { label: task.status, badge: 'tv-badge-gray', dot: '#ccc' };
  const tm = TAG_META[task.tag]       || { label: task.tag || '—', badge: 'tv-badge-gray' };
  const pm = PRIORITY_META[task.priority] || PRIORITY_META.normal;
  const deadlineLabel = taskDeadlineLabel(task);
  const creatorName = task?.creator?.name || '—';
  const assigneeName = task?.assignee?.name || '—';
  const priorityCls = task.priority === 'critical' ? 'is-critical' : task.priority === 'urgent' ? 'is-urgent' : '';
  const indicatorColor = task.priority === 'critical' ? 'var(--rose)' : task.priority === 'urgent' ? 'var(--amber)' : sm.dot;
  return (
    <div className={`tv-task-row ${priorityCls}`} onClick={() => onClick(task)}>
      <span className="tv-task-row-indicator" style={{ background: indicatorColor }} />
      <span className="tv-task-row-main">
        <span className="tv-task-row-subject">{task.subject || 'Без темы'}</span>
        <span className="tv-task-row-flow">
          <span className="tv-task-row-flow-name">{creatorName}</span>
          <span className="tv-task-row-flow-arrow">→</span>
          <span className="tv-task-row-flow-name">{assigneeName}</span>
        </span>
      </span>
      <span className="tv-task-row-meta">
        <span className={`tv-badge ${tm.badge}`}>{tm.label}</span>
        {task?.priority && task.priority !== 'normal' && <span className={`tv-badge ${pm.badge}`}>{pm.label}</span>}
        <span className={`tv-badge ${sm.badge}`}>{sm.label}</span>
        {deadlineLabel && <span className={`tv-deadline-chip ${isTaskOverdue(task) ? 'is-overdue' : ''}`}>{deadlineLabel}</span>}
        <span className="tv-task-row-assignee-chip">
          <AvatarCircle className="tv-avatar-xs" name={assigneeName} avatarUrl={task?.assignee?.avatar_url || ''} />
          <span className="tv-task-row-assignee-name">{assigneeName}</span>
        </span>
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
const TaskDrawer = React.memo(({
  task, onClose, actionLoadingKey,
  getActionButtons, openCompleteModal, openStatusModal, updateStatus, downloadAttachment,
  onEditTask, onDeleteTask, onTogglePinTask, onCopyTaskLink, onToggleChecklistItem, onSaveChecklistNote,
  isPinned,
}) => {
  const sm = STATUS_META[task.status] || { label: task.status, badge: 'tv-badge-gray' };
  const tm = TAG_META[task.tag]       || { label: task.tag || '—', badge: 'tv-badge-gray' };
  const pm = PRIORITY_META[task.priority] || PRIORITY_META.normal;
  const attachments     = Array.isArray(task.attachments)            ? task.attachments            : [];
  const compAttachments = Array.isArray(task.completion_attachments) ? task.completion_attachments : [];
  const history         = Array.isArray(task.history)                ? task.history                : [];
  const checklist       = Array.isArray(task.checklist)              ? task.checklist              : [];
  const progress        = checklistProgress(checklist);
  const deadlineLabel   = taskDeadlineLabel(task);
  const btns            = getActionButtons(task);
  const editBtn         = btns.find((btn) => btn.action === 'edit');
  const deleteBtn       = btns.find((btn) => btn.action === 'delete');
  const footerBtns      = btns.filter((btn) => btn.action !== 'edit' && btn.action !== 'delete');
  const assigneeId      = Number(task?.assignee?.id || 0);
  const creatorId       = Number(task?.creator?.id || 0);

  // ESC key handler
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const resolveHistorySide = useCallback((item) => {
    const changedById = Number(item?.changed_by || 0);
    if (changedById > 0) {
      if (changedById === assigneeId) return 'recipient';
      if (changedById === creatorId) return 'sender';
    }

    const statusCode = String(item?.status_code || '').trim();
    if (statusCode === 'in_progress' || statusCode === 'completed') return 'recipient';
    if (statusCode === 'assigned' || statusCode === 'accepted' || statusCode === 'returned' || statusCode === 'reopened') return 'sender';
    return 'neutral';
  }, [assigneeId, creatorId]);

  return (
    <>
      <div className="tv-overlay" onClick={onClose} />
      <div className="tv-drawer">
        <div className="tv-drawer-header">
          <div className="tv-drawer-header-text">
            <h2 className="tv-drawer-title">{task.subject || 'Без темы'}</h2>
            <div className="tv-drawer-badges">
              <span className={`tv-badge ${sm.badge}`}>{sm.label}</span>
              <span className={`tv-badge ${tm.badge}`}>{tm.label}</span>
              {task?.priority && <span className={`tv-badge ${pm.badge}`}>{pm.label}</span>}
              {task?.is_regulation && <span className="tv-badge tv-badge-teal">Регламент</span>}
            </div>
          </div>
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

        <div className="tv-drawer-body">
          {/* Participants */}
          <div className="tv-participants">
            <div className="tv-participant">
              <AvatarCircle
                className="tv-participant-avatar"
                name={task?.assignee?.name || '—'}
                avatarUrl={task?.assignee?.avatar_url || ''}
              />
              <div className="tv-participant-info">
                <div className="tv-participant-role">Исполнитель</div>
                <div className="tv-participant-name">{task?.assignee?.name || '—'}</div>
              </div>
            </div>
            <div className="tv-participant">
              <AvatarCircle
                className="tv-participant-avatar"
                name={task?.creator?.name || '—'}
                avatarUrl={task?.creator?.avatar_url || ''}
              />
              <div className="tv-participant-info">
                <div className="tv-participant-role">Постановщик</div>
                <div className="tv-participant-name">{task?.creator?.name || '—'}</div>
              </div>
            </div>
          </div>

          <div className="tv-info-grid">
            <div className="tv-info-item"><label>Создано</label><span>{fmt(task.created_at)}</span></div>
            <div className="tv-info-item"><label>Статус</label><span>{sm.label}</span></div>
            {deadlineLabel && (
              <div className="tv-info-item"><label>Дедлайн</label><span className={isTaskOverdue(task) ? 'tv-deadline-chip is-overdue' : ''}>{deadlineLabel}</span></div>
            )}
            <div className="tv-info-item"><label>Повторение</label><span>{task?.recurrence_type ? `${RECURRENCE_OPTIONS.find(item => item.value === task.recurrence_type)?.label || task.recurrence_type}${Number(task?.recurrence_interval || 1) > 1 ? ` · раз в ${task.recurrence_interval}` : ''}` : (task?.is_regulation ? 'Разовый регламент' : '—')}</span></div>
          </div>

          {task.description && (
            <>
              <hr className="tv-divider" />
              <div>
                <p className="tv-block-label">Описание</p>
                <p className="tv-description">{task.description}</p>
              </div>
            </>
          )}

          {attachments.length > 0 && (
            <>
              <hr className="tv-divider" />
              <div>
                <p className="tv-block-label">Файлы задачи</p>
                <div className="tv-file-list">
                  {attachments.map(att => (
                    <button key={att.id} className="tv-file-btn" onClick={() => downloadAttachment(att)}>
                      <FileIcon />{att.file_name}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

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
                            disabled={!!loading}
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

          {(task.completion_summary || compAttachments.length > 0) && (
            <>
              <hr className="tv-divider" />
              <div className="tv-completion-block">
                <p className="tv-block-label">Итоги выполнения</p>
                {task.completion_summary && (
                  <p className="tv-description" style={{ marginBottom: compAttachments.length ? 10 : 0 }}>
                    {task.completion_summary}
                  </p>
                )}
                {compAttachments.length > 0 && (
                  <div className="tv-file-list">
                    {compAttachments.map(att => (
                      <button key={att.id} className="tv-file-btn"
                        style={{ background: '#e0e7ff', borderColor: '#c7d2fe', color: '#3730a3' }}
                        onClick={() => downloadAttachment(att)}>
                        <FileIcon />{att.file_name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
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
          <div className="tv-drawer-footer">
            <span style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--ink-3)' }}>
              <span className="tv-kbd">Esc</span> закрыть
            </span>
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
                    updateStatus(task.id, btn.action);
                  }}
                >
                  {loading ? 'Сохраняю...' : btn.label}
                </button>
              );
            })}
          </div>
        )}
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
  const [paletteId, setPaletteId] = useState(() => {
    if (typeof window === 'undefined') return PIN_PALETTES[0].id;
    try {
      return window.localStorage.getItem(PIN_PALETTE_STORAGE_KEY) || PIN_PALETTES[0].id;
    } catch (error) {
      return PIN_PALETTES[0].id;
    }
  });
  const [selectedMenuPersonKey, setSelectedMenuPersonKey] = useState(null);
  const [pipContainer, setPipContainer] = useState(null);
  const [pipWindow, setPipWindow] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const widgetRef = useRef(null);
  const dragStateRef = useRef(null);
  const latestPositionRef = useRef(position);
  const currentUserRole = normalizeRole(user?.role);
  const currentUserId = Number(user?.id || 0);
  const sm = STATUS_META[task?.status] || { label: task?.status || '—', badge: 'tv-badge-gray' };
  const tm = TAG_META[task?.tag] || { label: task?.tag || '—', badge: 'tv-badge-gray' };
  const pm = PRIORITY_META[task?.priority] || PRIORITY_META.normal;
  const attachments = Array.isArray(task?.attachments) ? task.attachments : [];
  const compAttachments = Array.isArray(task?.completion_attachments) ? task.completion_attachments : [];
  const checklist = Array.isArray(task?.checklist) ? task.checklist : [];
  const progress = checklistProgress(checklist);
  const activePalette = PIN_PALETTES.find((item) => item.id === paletteId) || PIN_PALETTES[0];
  const canUseDocumentPictureInPicture = typeof window !== 'undefined' && Boolean(window.documentPictureInPicture?.requestWindow);
  const actionButtons = useMemo(
    () => buildTaskActionButtons(task, currentUserId, currentUserRole)
      .filter((btn) => !['edit', 'delete'].includes(btn.action)),
    [task, currentUserId, currentUserRole]
  );
  const managementButtons = useMemo(
    () => buildTaskActionButtons(task, currentUserId, currentUserRole)
      .filter((btn) => ['edit', 'delete'].includes(btn.action)),
    [task, currentUserId, currentUserRole]
  );
  const canEditTask = managementButtons.some((btn) => btn.action === 'edit');
  const canDeleteTask = managementButtons.some((btn) => btn.action === 'delete');
  const recipientOptions = useMemo(() => {
    const unique = new Map();
    const addRecipient = (person) => {
      const id = Number(person?.id || 0);
      if (!id || unique.has(id)) return;
      unique.set(id, person);
    };
    (Array.isArray(taskRecipients) ? taskRecipients : []).forEach(addRecipient);
    addRecipient(task?.assignee);
    return Array.from(unique.values());
  }, [taskRecipients, task?.assignee]);
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
    () => taskOptions.filter((item) => Number(item?.assignee?.id || 0) === currentUserId),
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
    () => groupTasksByPerson(outgoingTaskOptions, (item) => item?.assignee),
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
    const rect = widgetRef.current.getBoundingClientRect();
    dragStateRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setIsDragging(true);
  }, []);

  const handlePointerMove = useCallback((event) => {
    const drag = dragStateRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
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
    setIsDragging(false);
    onStateChange?.({
      expanded,
      position: latestPositionRef.current,
    });
  }, [expanded, onStateChange]);

  const toggleExpanded = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      onStateChange?.({
        expanded: next,
        position: latestPositionRef.current,
      });
      return next;
    });
  }, [onStateChange]);

  useEffect(() => {
    latestPositionRef.current = position;
  }, [position]);

  useEffect(() => {
    setExpanded(Boolean(initialExpanded));
  }, [initialExpanded, task?.id]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(PIN_PALETTE_STORAGE_KEY, activePalette.id);
    } catch (error) {
      // Palette preference is best-effort browser state.
    }
  }, [activePalette.id]);

  useEffect(() => {
    if (!pipWindow) return;
    pipWindow.document.body.style.background = activePalette.vars['--bg'] || '#f4f3f0';
  }, [pipWindow, activePalette]);

  const openQuickCreate = useCallback(() => {
    setQuickForm(buildEmptyTaskForm({
      assignedTo: String(task?.assignee?.id || currentUserId || ''),
      priority: task?.priority || 'normal',
    }));
    setQuickFormFiles([]);
    setQuickFormError('');
    setQuickFormMode('create');
    setExpanded(true);
    onStateChange?.({ expanded: true, position: latestPositionRef.current });
    setNoteOpen(false);
    setPaletteOpen(false);
    setTaskMenuOpen(false);
  }, [currentUserId, onStateChange, task?.assignee?.id, task?.priority]);

  const openQuickEdit = useCallback(() => {
    if (!task?.id) return;
    setQuickForm(taskToTaskForm(task, currentUserId));
    setQuickFormFiles([]);
    setQuickFormError('');
    setQuickFormMode('edit');
    setExpanded(true);
    onStateChange?.({ expanded: true, position: latestPositionRef.current });
    setNoteOpen(false);
    setPaletteOpen(false);
    setTaskMenuOpen(false);
  }, [currentUserId, onStateChange, task]);

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
    if (!quickForm.assignedTo) {
      setQuickFormError('Выберите исполнителя');
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
      const currentPersonId = taskMenuScope === 'outgoing'
        ? Number(task?.assignee?.id || 0)
        : Number(task?.creator?.id || 0);
      const matchingGroup = taskMenuGroups.find((group) => Number(group?.personId || 0) === currentPersonId);
      return matchingGroup?.key || taskMenuGroups[0].key;
    });
  }, [task?.assignee?.id, task?.creator?.id, taskMenuGroups, taskMenuScope]);

  useEffect(() => {
    if (!pipWindow) return undefined;
    const handlePageHide = () => {
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
    setExpanded(true);
    onStateChange?.({
      expanded: true,
      position: latestPositionRef.current,
    });
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
      Array.from(document.querySelectorAll('link[rel="stylesheet"], style')).forEach((node) => {
        nextPipWindow.document.head.appendChild(node.cloneNode(true));
      });
      nextPipWindow.document.body.style.margin = '0';
      nextPipWindow.document.body.style.background = activePalette.vars['--bg'] || '#f4f3f0';
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

  if (!task?.id) return null;

  const pinHeaderKicker = noteOpen
    ? 'Локальные заметки'
    : quickFormMode === 'create'
      ? 'Создание задачи'
      : quickFormMode === 'edit'
        ? 'Редактирование задачи'
        : 'Закрепленная задача';
  const pinHeaderTitle = noteOpen
    ? 'Темы и заметки'
    : quickFormMode === 'create'
      ? 'Новая задача'
      : quickFormMode === 'edit'
        ? (task.subject || 'Редактирование')
        : (task.subject || 'Без темы');

  const quickTaskForm = quickFormMode ? (
    <form className={`tv-pin-task-form ${pipWindow ? 'is-fullscreen' : ''}`} onSubmit={submitQuickForm}>
      <p className="tv-pin-task-form-title">
        {quickFormMode === 'edit' ? 'Редактирование задачи' : 'Новая задача'}
      </p>
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
      <div className="tv-pin-task-form-grid">
        <div className="tv-form-field">
          <label>Тип</label>
          <select
            className="tv-select"
            value={quickForm.tag}
            disabled={quickFormLoading}
            onChange={(event) => updateQuickFormField('tag', event.target.value)}
          >
            {TAG_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </div>
        <div className="tv-form-field">
          <label>Срочность</label>
          <select
            className="tv-select"
            value={quickForm.priority}
            disabled={quickFormLoading}
            onChange={(event) => updateQuickFormField('priority', event.target.value)}
          >
            {PRIORITY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </div>
      </div>
      <div className="tv-form-field">
        <label>Исполнитель *</label>
        <select
          className="tv-select"
          value={quickForm.assignedTo}
          disabled={quickFormLoading || isTaskRecipientsLoading}
          onChange={(event) => updateQuickFormField('assignedTo', event.target.value)}
        >
          <option value="">{isTaskRecipientsLoading ? 'Загрузка...' : 'Выберите сотрудника'}</option>
          {recipientOptions.map((recipient) => (
            <option key={recipient.id} value={recipient.id}>
              {recipient.name} ({ROLE_LABELS[recipient.role] || recipient.role})
            </option>
          ))}
        </select>
      </div>
      <div className="tv-form-field">
        <label>Дедлайн через</label>
        <div className="tv-form-inline-grid">
          <input className="tv-input" type="number" min="0" placeholder="Дней" value={quickForm.deadlineDays} disabled={quickFormLoading}
            onChange={(event) => updateQuickFormField('deadlineDays', event.target.value)} />
          <input className="tv-input" type="number" min="0" max="23" placeholder="Часов" value={quickForm.deadlineHours} disabled={quickFormLoading}
            onChange={(event) => updateQuickFormField('deadlineHours', event.target.value)} />
          <input className="tv-input" type="number" min="0" max="59" placeholder="Минут" value={quickForm.deadlineMinutes} disabled={quickFormLoading}
            onChange={(event) => updateQuickFormField('deadlineMinutes', event.target.value)} />
        </div>
      </div>
      <div className="tv-soft-block">
        <label className="tv-form-switch">
          <input
            type="checkbox"
            checked={!!quickForm.isRegulation}
            disabled={quickFormLoading}
            onChange={(event) => setQuickForm((prev) => ({
              ...prev,
              isRegulation: event.target.checked,
              recurrenceType: event.target.checked ? (prev.recurrenceType || 'daily') : ''
            }))}
          />
          Регламентная задача
        </label>
        {quickForm.isRegulation && (
          <div className="tv-pin-task-form-grid" style={{ marginTop: 8 }}>
            <div className="tv-form-field">
              <label>Период</label>
              <select
                className="tv-select"
                value={quickForm.recurrenceType || 'daily'}
                disabled={quickFormLoading}
                onChange={(event) => updateQuickFormField('recurrenceType', event.target.value)}
              >
                {RECURRENCE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
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
      <div className="tv-form-field">
        <label>Чек-лист</label>
        <ChecklistDraftEditor
          compact
          items={quickForm.checklistItems}
          disabled={quickFormLoading}
          onChange={updateQuickFormChecklist}
        />
      </div>
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
        <div className="tv-soft-block tv-pin-file-section">
          <p className="tv-block-label">Прикрепленные файлы</p>
          <div className="tv-file-list">
            {attachments.map((att) => (
              <button
                key={att.id}
                type="button"
                className="tv-file-btn"
                onClick={() => onDownloadAttachment?.(att)}
              >
                <FileIcon />
                {att.file_name}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {quickFormError && <span className="tv-pin-form-error">{quickFormError}</span>}
      <div className="tv-pin-task-form-actions">
        <button type="button" className="tv-btn tv-btn-ghost" disabled={quickFormLoading} onClick={closeQuickForm}>
          Отмена
        </button>
        <button type="submit" className="tv-btn tv-btn-primary" disabled={quickFormLoading || isTaskRecipientsLoading}>
          {quickFormLoading ? 'Сохраняю...' : 'Сохранить'}
        </button>
      </div>
    </form>
  ) : null;

  const widgetMarkup = (
    <section
      ref={widgetRef}
      className={`tv-pin-widget ${isDragging ? 'is-dragging' : ''} ${pipWindow ? 'is-detached' : ''}`}
      style={{
        ...activePalette.vars,
        ...(pipWindow ? {} : (position ? { left: position.x, top: position.y } : { right: 18, bottom: 18 }))
      }}
      aria-label="Закрепленная задача"
    >
      <header className="tv-pin-header">
        {!pipWindow && (
          <button
            type="button"
            className="tv-pin-drag-handle"
            aria-label="Перетащить закрепленную задачу"
            title="Перетащить"
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerEnd}
            onPointerCancel={handlePointerEnd}
          >
            <GripHorizontal size={16} strokeWidth={2} />
          </button>
        )}
        <div className="tv-pin-heading">
          <span className="tv-pin-kicker">{pinHeaderKicker}</span>
          <h2 className="tv-pin-title">{pinHeaderTitle}</h2>
        </div>
        <div className="tv-pin-header-actions">
          <button
            type="button"
            className={`tv-pin-header-btn ${noteOpen ? 'is-active' : ''}`}
            title={noteOpen ? 'Скрыть заметки' : 'Открыть заметки'}
            aria-label={noteOpen ? 'Скрыть заметки' : 'Открыть заметки'}
            onClick={() => {
              setNoteOpen((prev) => !prev);
              setPaletteOpen(false);
              setTaskMenuOpen(false);
            }}
          >
            <StickyNote size={15} strokeWidth={2} />
          </button>
          <button
            type="button"
            className={`tv-pin-header-btn ${paletteOpen ? 'is-active' : ''}`}
            title="Палитра закрепленной задачи"
            aria-label="Палитра закрепленной задачи"
            onClick={() => {
              setPaletteOpen((prev) => !prev);
              setNoteOpen(false);
              setTaskMenuOpen(false);
            }}
          >
            <Palette size={15} strokeWidth={2} />
          </button>
          {!pipWindow && canUseDocumentPictureInPicture && (
            <button
              type="button"
              className="tv-pin-header-btn"
              title="Открыть поверх окон"
              aria-label="Открыть поверх окон"
              onClick={openDocumentPictureInPicture}
            >
              <PictureInPicture2 size={15} strokeWidth={2} />
            </button>
          )}
          <button
            type="button"
            className="tv-pin-header-btn"
            title={expanded ? 'Свернуть' : 'Развернуть'}
            aria-label={expanded ? 'Свернуть' : 'Развернуть'}
            onClick={toggleExpanded}
          >
            {expanded ? <Minimize2 size={15} strokeWidth={2} /> : <Maximize2 size={15} strokeWidth={2} />}
          </button>
          <button
            type="button"
            className="tv-pin-header-btn"
            title="Открыть карточку задачи"
            aria-label="Открыть карточку задачи"
            onClick={() => onOpenDetails?.(task)}
          >
            <PanelRightOpen size={15} strokeWidth={2} />
          </button>
          <button
            type="button"
            className="tv-pin-header-btn tv-pin-header-btn-danger"
            title="Открепить задачу"
            aria-label="Открепить задачу"
            onClick={() => onUnpin?.()}
          >
            <PinOff size={15} strokeWidth={2} />
          </button>
        </div>
      </header>

      <div className={`tv-pin-body ${taskMenuOpen ? 'is-menu-open' : ''} ${noteOpen ? 'is-notes-open' : ''} ${quickFormMode ? 'is-task-form-open' : ''}`}>
        {!noteOpen && !quickFormMode && (
        <div className="tv-pin-menu-head">
          <button
            type="button"
            className="tv-pin-menu-trigger"
            title={taskMenuOpen ? 'Вернуться к задаче' : 'Открыть меню задач'}
            aria-label={taskMenuOpen ? 'Вернуться к задаче' : 'Открыть меню задач'}
            onClick={() => setTaskMenuOpen((prev) => !prev)}
          >
            <LucideChevronLeft size={15} strokeWidth={2} />
          </button>
          <p className="tv-pin-menu-title">{taskMenuOpen ? 'Меню задач' : 'Текущая задача'}</p>
          {taskMenuOpen ? (
            <div className="tv-pin-menu-head-actions">
              {onCreateTask && (
                <button
                  type="button"
                  className="tv-btn tv-btn-primary tv-pin-menu-create-btn"
                  disabled={!!actionLoadingKey || quickFormLoading}
                  onClick={openQuickCreate}
                >
                  <PlusIcon /> Новая
                </button>
              )}
            </div>
          ) : (
            <div className="tv-pin-badges">
              <span className={`tv-badge ${sm.badge}`}>{sm.label}</span>
              <span className={`tv-badge ${tm.badge}`}>{tm.label}</span>
              {task?.priority && <span className={`tv-badge ${pm.badge}`}>{pm.label}</span>}
              {task?.is_regulation && <span className="tv-badge tv-badge-teal">Регламент</span>}
            </div>
          )}
        </div>
        )}

        {noteOpen ? (
          <TaskNotesPanel notesState={notesState} compact fullScreen={!!pipWindow} />
        ) : quickFormMode ? (
          quickTaskForm
        ) : paletteOpen ? (
          <div className="tv-pin-note-panel">
            <p className="tv-block-label" style={{ margin: 0 }}>Палитра PiP</p>
            <div className="tv-pin-palette-row">
              {PIN_PALETTES.map((palette) => (
                <button
                  key={palette.id}
                  type="button"
                  className={`tv-pin-palette-btn ${palette.id === activePalette.id ? 'is-active' : ''}`}
                  title={palette.label}
                  aria-label={palette.label}
                  onClick={() => setPaletteId(palette.id)}
                >
                  <span className="tv-pin-palette-swatch" style={{ background: palette.vars['--surface-2'] }} />
                  <span className="tv-pin-palette-swatch" style={{ background: palette.vars['--surface'] }} />
                  <span className="tv-pin-palette-swatch" style={{ background: palette.vars['--accent'] }} />
                </button>
              ))}
            </div>
          </div>
        ) : taskMenuOpen ? (
          <div className="tv-pin-task-menu-shell">
            <div className="tv-pin-menu-tabs" role="tablist" aria-label="Фильтр задач">
              <button
                type="button"
                className={`tv-pin-menu-tab ${taskMenuScope === 'incoming' ? 'is-active' : ''}`}
                onClick={() => setTaskMenuScope('incoming')}
              >
                Входящие
                <span className="tv-pin-menu-tab-count">{incomingTaskOptions.length}</span>
              </button>
              <button
                type="button"
                className={`tv-pin-menu-tab ${taskMenuScope === 'outgoing' ? 'is-active' : ''}`}
                onClick={() => setTaskMenuScope('outgoing')}
              >
                Исходящие
                <span className="tv-pin-menu-tab-count">{outgoingTaskOptions.length}</span>
              </button>
            </div>
            <div className="tv-pin-task-menu">
            <div className="tv-pin-people-rail">
              {taskMenuGroups.map((group) => (
                <button
                  key={group.key}
                  type="button"
                  className={`tv-pin-person-btn ${activeTaskMenuGroup?.key === group.key ? 'is-active' : ''}`}
                  title={group.name}
                  aria-label={group.name}
                  onClick={() => setSelectedMenuPersonKey(group.key)}
                >
                  <AvatarCircle className="tv-avatar-md" name={group.name} avatarUrl={group.avatarUrl} />
                  {(group.active + group.notAccepted) > 0 && (
                    <span
                      className={`tv-pin-person-count ${group.notAccepted > 0 ? 'is-alert' : 'is-active'}`}
                      aria-hidden="true"
                    >
                      {(group.active + group.notAccepted) > 9 ? '9+' : (group.active + group.notAccepted)}
                    </span>
                  )}
                </button>
              ))}
            </div>
            <div className="tv-pin-menu-panel">
              {activeTaskMenuGroup ? (
                <>
                  <div className="tv-pin-person-summary">
                    <span className="tv-pin-person-summary-name">{activeTaskMenuGroup.name}</span>
                    <span className="tv-pin-person-summary-stats">
                      <span className="tv-pin-person-summary-chip">
                        <span className="tv-task-count">{activeTaskMenuGroup.done}</span> выполнено
                      </span>
                      <span className="tv-pin-person-summary-chip">
                        <span className="tv-task-count">{activeTaskMenuGroup.active}</span> в работе
                      </span>
                      <span className="tv-pin-person-summary-chip">
                        <span className={`tv-task-count ${activeTaskMenuGroup.notAccepted > 0 ? 'is-alert' : ''}`}>
                          {activeTaskMenuGroup.notAccepted}
                        </span> ждут принятия
                      </span>
                    </span>
                  </div>
                  <div className="tv-pin-person-task-list">
                    {activeTaskMenuGroup.tasks.map((item) => {
                      const itemStatus = STATUS_META[item.status] || { label: item.status || '—', badge: 'tv-badge-gray' };
                      return (
                        <button
                          key={item.id}
                          type="button"
                          className={`tv-pin-person-task ${Number(item?.id || 0) === Number(task.id) ? 'is-active' : ''}`}
                          onClick={() => {
                            onSelectTask?.(item);
                            setTaskMenuOpen(false);
                          }}
                        >
                          <span className="tv-pin-person-task-title">{item.subject || `Задача #${item.id}`}</span>
                          <span className="tv-pin-person-task-meta">
                            <span className={`tv-badge ${itemStatus.badge}`}>{itemStatus.label}</span>
                            <span className="tv-task-row-date">{fmt(item.created_at)}</span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </>
              ) : (
                <span className="tv-pin-empty-actions">
                  {isTasksLoading
                    ? 'Загружаю задачи...'
                    : (taskMenuScope === 'outgoing' ? 'Исходящих задач пока нет.' : 'Входящих задач пока нет.')}
                </span>
              )}
            </div>
            </div>
          </div>
        ) : (
          <>
            {expanded && (
              <div className="tv-pin-summary">
                <p className="tv-pin-description">
                  {task.description || 'Описание не добавлено.'}
                </p>
                <div className="tv-pin-meta">
                  <div className="tv-pin-meta-item">
                    <span className="tv-pin-meta-label">Исполнитель</span>
                    <span className="tv-pin-meta-value">{task?.assignee?.name || '—'}</span>
                  </div>
                  <div className="tv-pin-meta-item">
                    <span className="tv-pin-meta-label">Постановщик</span>
                    <span className="tv-pin-meta-value">{task?.creator?.name || '—'}</span>
                  </div>
                  {task?.due_at && (
                    <div className="tv-pin-meta-item">
                      <span className="tv-pin-meta-label">Дедлайн</span>
                      <span className="tv-pin-meta-value">{taskDeadlineLabel(task)}</span>
                    </div>
                  )}
                  {progress.total > 0 && (
                    <div className="tv-pin-meta-item">
                      <span className="tv-pin-meta-label">Чек-лист</span>
                      <span className="tv-pin-meta-value">{`${progress.done}/${progress.total}`}</span>
                    </div>
                  )}
                </div>
                {checklist.length > 0 && (
                  <div className="tv-pin-mini-checklist">
                    {checklist.slice(0, 5).map((item) => (
                      <label key={item.id} className={`tv-checklist-row ${item.is_done ? 'is-done' : ''}`}>
                        <input
                          type="checkbox"
                          className="tv-checklist-checkbox"
                          checked={!!item.is_done}
                          disabled={actionLoadingKey === `${task.id}:checklist:${item.id}`}
                          onChange={() => onToggleChecklistItem?.(task, item, !item.is_done)}
                        />
                        <span className="tv-checklist-title">{item.title}</span>
                      </label>
                    ))}
                    {checklist.length > 5 && (
                      <span className="tv-pin-empty-actions">Ещё пунктов: {checklist.length - 5}</span>
                    )}
                  </div>
                )}
                {(attachments.length > 0 || compAttachments.length > 0) && (
                  <div className="tv-pin-files">
                    {attachments.length > 0 && (
                      <div className="tv-pin-file-section">
                        <p className="tv-block-label">Файлы задачи</p>
                        <div className="tv-file-list">
                          {attachments.map((att) => (
                            <button
                              key={att.id}
                              type="button"
                              className="tv-file-btn"
                              onClick={() => onDownloadAttachment?.(att)}
                            >
                              <FileIcon />
                              {att.file_name}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {compAttachments.length > 0 && (
                      <div className="tv-pin-file-section tv-pin-file-section-result">
                        <p className="tv-block-label">Файлы выполнения</p>
                        <div className="tv-file-list">
                          {compAttachments.map((att) => (
                            <button
                              key={att.id}
                              type="button"
                              className="tv-file-btn"
                              onClick={() => onDownloadAttachment?.(att)}
                            >
                              <FileIcon />
                              {att.file_name}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            <div className="tv-pin-actions">
              {canEditTask && onEditTask && (
                <button
                  type="button"
                  className="tv-btn tv-btn-ghost"
                  disabled={!!actionLoadingKey || quickFormLoading}
                  onClick={openQuickEdit}
                >
                  Редактировать
                </button>
              )}
              {canDeleteTask && onDeleteTask && (
                <button
                  type="button"
                  className="tv-btn tv-btn-rose"
                  disabled={!!actionLoadingKey || quickFormLoading}
                  onClick={handleQuickDelete}
                >
                  Удалить
                </button>
              )}
              {actionButtons.map((btn) => {
                const loading = actionLoadingKey === `${task.id}:${btn.action}`;
                return (
                  <button
                    key={btn.action}
                    type="button"
                    className={`tv-btn ${btn.cls}`}
                    disabled={!!actionLoadingKey}
                    onClick={() => onRunAction?.(task, btn.action)}
                  >
                    {loading && <LoaderCircle size={14} strokeWidth={2} className="animate-spin" />}
                    {loading ? 'Сохраняю...' : btn.label}
                  </button>
                );
              })}
              {!(canEditTask && onEditTask) && !(canDeleteTask && onDeleteTask) && actionButtons.length === 0 && (
                <span className="tv-pin-empty-actions">Для текущего статуса быстрых действий нет.</span>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );

  if (pipContainer) return createPortal(widgetMarkup, pipContainer);
  return canUseDocumentPictureInPicture ? null : widgetMarkup;
});

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
}) => {
  const currentUserRole = normalizeRole(user?.role);
  const canAccessTasks = isAdminLikeRole(currentUserRole) || isSupervisorRole(currentUserRole) || currentUserRole === 'trainer';
  const [tasks,               setTasks]               = useState([]);
  const [pagedTasks,          setPagedTasks]          = useState([]);
  const [recipients,          setRecipients]          = useState([]);
  const [isTasksLoading,      setIsTasksLoading]      = useState(false);
  const [isPagedTasksLoading, setIsPagedTasksLoading] = useState(false);
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
  const [completeModal,     setCompleteModal]     = useState({ open: false, taskId: null, taskSubject: '' });
  const [statusModal,       setStatusModal]       = useState({ open: false, taskId: null, action: '', taskSubject: '' });
  const [completionSummary, setCompletionSummary] = useState('');
  const [completionFiles,   setCompletionFiles]   = useState([]);
  const [statusComment,     setStatusComment]     = useState('');
  const [statusFiles,       setStatusFiles]       = useState([]);
  const [myTasksTab,        setMyTasksTab]        = useState('incoming');
  const [incomingPersonKey, setIncomingPersonKey] = useState(null);
  const [outgoingPersonKey, setOutgoingPersonKey] = useState(null);

  const fileInputRef           = useRef(null);
  const completionFileInputRef = useRef(null);
  const statusFileInputRef     = useRef(null);
  const searchRef              = useRef(null);
  const pagedRequestIdRef      = useRef(0);
  const personTasksRequestIdRef = useRef(0);
  const handledFocusRequestRef = useRef(0);
  const hasSyncedDrawerUrlRef   = useRef(false);
  const [form, setForm] = useState(() => buildEmptyTaskForm());
  const [notesOpen, setNotesOpen] = useState(false);

  const showToastRef = useRef(showToast);
  useEffect(() => { showToastRef.current = showToast; }, [showToast]);

  const notify = useCallback((msg, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(msg, type);
  }, []);

  const copyTaskLink = useCallback(async (task) => {
    const taskLink = buildTaskDeepLink(task?.id);
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

  const fetchRecipients = useCallback(async () => {
    setIsRecipientsLoading(true);
    try {
      const res = await axios.get(`${apiBaseUrl}/api/tasks/recipients`, { headers: buildHeaders() });
      setRecipients(Array.isArray(res?.data?.recipients) ? res.data.recipients : []);
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось загрузить список сотрудников', 'error');
    } finally { setIsRecipientsLoading(false); }
  }, [apiBaseUrl, buildHeaders, notify]);

  const fetchTasks = useCallback(async () => {
    setIsTasksLoading(true);
    try {
      const res  = await axios.get(`${apiBaseUrl}/api/tasks`, { headers: buildHeaders() });
      const list = Array.isArray(res?.data?.tasks) ? res.data.tasks : [];
      setTasks(list);
      setDrawerTask(prev => prev ? (list.find(t => t.id === prev.id) ?? prev) : null);
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось загрузить задачи', 'error');
    } finally { setIsTasksLoading(false); }
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
          const currentTaskId = Number(new URL(window.location.href).searchParams.get(TASK_ID_QUERY_PARAM) || 0);
          if (currentTaskId > 0) return;
        } catch (error) {
          // Fall through to the regular sync path.
        }
      }
    }
    syncTaskDeepLink(drawerTask?.id || null);
  }, [drawerTask?.id]);

  const fetchPagedTasks = useCallback(async () => {
    const requestId = ++pagedRequestIdRef.current;
    setIsPagedTasksLoading(true);
    try {
      const params = {
        limit: TASKS_PAGE_SIZE,
        offset: (allTasksPage - 1) * TASKS_PAGE_SIZE
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
    filterStatus,
    filterTag,
    filterPriority,
  ]);

  useEffect(() => {
    if (!user || !canAccessTasks) return;
    fetchRecipients();
    fetchTasks();
  }, [user, canAccessTasks, fetchRecipients, fetchTasks]);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery.trim());
    }, 280);
    return () => clearTimeout(t);
  }, [searchQuery]);

  useEffect(() => {
    if (!user || !canAccessTasks) return;
    fetchPagedTasks();
  }, [user, canAccessTasks, fetchPagedTasks]);

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

  const incomingTasks = useMemo(
    () => tasks.filter(t => Number(t?.assignee?.id || 0) === currentUserId),
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
    () => groupTasksByPerson(outgoingTasks, t => t?.assignee),
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
    const jobs = [fetchTasks(), fetchPagedTasks()];
    if (isPersonDrilldown) jobs.push(fetchPersonTasks());
    await Promise.all(jobs);
  }, [fetchTasks, fetchPagedTasks, fetchPersonTasks, isPersonDrilldown]);

  const patchTaskEverywhere = useCallback((taskId, updater) => {
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
      if (res?.data?.warning) notify(res.data.warning, 'error');
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось обновить чек-лист', 'error');
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, patchTaskEverywhere]);

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
      if (res?.data?.warning) notify(res.data.warning, 'error');
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось сохранить итог', 'error');
    } finally {
      setActionLoadingKey('');
    }
  }, [apiBaseUrl, buildHeaders, notify, patchTaskEverywhere]);

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

  useEffect(() => {
    const requestId = Number(focusTaskRequest?.requestId || 0);
    const taskId = Number(focusTaskRequest?.taskId || 0);
    if (!requestId || !taskId || handledFocusRequestRef.current === requestId) return;
    const nextTask = [...tasks, ...pagedTasks, ...personTasks]
      .find((task) => Number(task?.id || 0) === taskId);
    if (!nextTask) return;
    handledFocusRequestRef.current = requestId;
    setDrawerTask(nextTask);
  }, [focusTaskRequest, tasks, pagedTasks, personTasks]);

  // Stats
  const stats = useMemo(() => ({
    total:      tasks.length,
    inProgress: tasks.filter(t => t.status === 'in_progress').length,
    completed:  tasks.filter(t => t.status === 'completed' || t.status === 'accepted').length,
    overdue:    tasks.filter(t => t.status === 'returned').length,
  }), [tasks]);

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

  /* ── Create ── */
  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.subject.trim()) { notify('Укажите тему задачи', 'error'); return; }
    if (!form.assignedTo)     { notify('Выберите сотрудника', 'error'); return; }

    const body = new FormData();
    appendTaskFormData(body, form);
    selectedFiles.forEach(f => body.append('files', f));

    setIsCreateLoading(true);
    try {
      const res = await axios.post(`${apiBaseUrl}/api/tasks`, body, { headers: buildHeaders() });
      notify(res?.data?.message || 'Задача создана');
      if (res?.data?.warning) notify(res.data.warning, 'error');
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
    setEditForm(taskToTaskForm(task));
    setEditModal({
      open: true,
      taskId: task.id,
      taskSubject: task.subject || ''
    });
  }, []);

  const closeEditModal = useCallback(() => {
    setEditModal({ open: false, taskId: null, taskSubject: '' });
    setEditForm(buildEmptyTaskForm());
  }, []);

  const submitEditTask = useCallback(async (e) => {
    e.preventDefault();
    if (!editModal.taskId) return;
    if (!editForm.subject.trim()) { notify('Укажите тему задачи', 'error'); return; }
    if (!editForm.assignedTo) { notify('Выберите исполнителя', 'error'); return; }

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
  }, [editModal, editForm, apiBaseUrl, buildHeaders, notify, closeEditModal, refreshTasksData]);

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

  /* ── Complete modal ── */
  const openCompleteModal = useCallback((task) => {
    if (!task?.id) return;
    setCompletionSummary(task?.completion_summary || '');
    setCompletionFiles([]);
    if (completionFileInputRef.current) completionFileInputRef.current.value = '';
    setCompleteModal({ open: true, taskId: task.id, taskSubject: task.subject || '' });
  }, []);

  const closeCompleteModal = useCallback(() => {
    setCompleteModal({ open: false, taskId: null, taskSubject: '' });
    setCompletionSummary('');
    setCompletionFiles([]);
    if (completionFileInputRef.current) completionFileInputRef.current.value = '';
  }, []);

  const openStatusModal = useCallback((task, action) => {
    if (!task?.id) return;
    if (action !== 'returned' && action !== 'reopened') return;
    setStatusComment('');
    setStatusFiles([]);
    if (statusFileInputRef.current) statusFileInputRef.current.value = '';
    setStatusModal({ open: true, taskId: task.id, action, taskSubject: task.subject || '' });
  }, []);

  const closeStatusModal = useCallback(() => {
    setStatusModal({ open: false, taskId: null, action: '', taskSubject: '' });
    setStatusComment('');
    setStatusFiles([]);
    if (statusFileInputRef.current) statusFileInputRef.current.value = '';
  }, []);

  const submitComplete = useCallback(async (e) => {
    e.preventDefault();
    if (!completeModal.taskId) return;
    const key = `${completeModal.taskId}:completed`;
    setActionLoadingKey(key);
    try {
      const body = new FormData();
      body.append('action', 'completed');
      body.append('completion_summary', completionSummary.trim());
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
  }, [completeModal, completionSummary, completionFiles, apiBaseUrl, buildHeaders, notify, closeCompleteModal, refreshTasksData]);

  const submitStatusModal = useCallback(async (e) => {
    e.preventDefault();
    if (!statusModal.taskId || !statusModal.action) return;
    const ok = await updateStatus(statusModal.taskId, statusModal.action, {
      comment: statusComment,
      files: statusFiles,
    });
    if (ok) closeStatusModal();
  }, [statusModal, statusComment, statusFiles, updateStatus, closeStatusModal]);

  /* ── Download ── */
  const downloadAttachment = useCallback(async (att) => {
    try {
      const res = await axios.get(
        `${apiBaseUrl}/api/tasks/attachments/${att.id}/download`,
        { headers: buildHeaders(), responseType: 'blob' }
      );
      const blob = new Blob([res.data], { type: att.content_type || 'application/octet-stream' });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href = url; a.download = att.file_name || `attachment-${att.id}`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось скачать файл', 'error');
    }
  }, [apiBaseUrl, buildHeaders, notify]);

  /* ── Action buttons ── */
  const getActionButtons = useCallback((task) => {
    return buildTaskActionButtons(task, currentUserId, currentUserRole);
  }, [currentUserId, currentUserRole]);

  const isReturnAction = statusModal.action === 'returned';
  const statusModalTitle = isReturnAction ? 'Возврат на доработку' : 'Возобновление задачи';
  const statusModalCommentLabel = isReturnAction ? 'Комментарий по доработке' : 'Комментарий к возобновлению';
  const statusModalSubmitLabel = isReturnAction ? 'Вернуть на доработку' : 'Возобновить задачу';
  const editModalLoadingKey = `${editModal.taskId}:edit`;
  const deleteModalLoadingKey = `${deleteModal.taskId}:delete`;
  const statusModalLoadingKey = `${statusModal.taskId}:${statusModal.action}`;

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
              onClick={setDrawerTask}
              onPin={(task) => {
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
            <span className="tv-person-tasks-label">{selected.name} · {selectedTotal} задач</span>
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
    <div className="tv-root">
      {/* Top bar */}
      <div className="tv-topbar">
        <h1 className="tv-topbar-title">Задачи</h1>
        <div className="tv-topbar-actions">
          <button
            className={`tv-btn ${notesOpen ? 'tv-btn-amber' : 'tv-btn-ghost'}`}
            type="button"
            onClick={() => setNotesOpen((prev) => !prev)}
          >
            <StickyNote size={13} strokeWidth={2} />
            Заметки
          </button>
          <button className="tv-btn tv-btn-ghost" onClick={refreshTasksData} disabled={isAnyTasksLoading}>
            <RefreshCw size={13} strokeWidth={2} style={{ transition: 'transform .4s', transform: isAnyTasksLoading ? 'rotate(360deg)' : 'none' }} />
            {isAnyTasksLoading ? 'Обновляю...' : 'Обновить'}
          </button>
          <button className="tv-btn tv-btn-primary" onClick={() => setCreateOpen(true)}>
            <PlusIcon /> Новая задача
          </button>
        </div>
      </div>

      {notesOpen && (
        <div className="tv-section">
          <div className="tv-section-header">
            <span className="tv-section-title heading">Заметки</span>
          </div>
          <TaskNotesPanel notesState={notesState} />
        </div>
      )}

      {/* Stats strip */}
      <div className="tv-stats-strip">
        <div className="tv-stat-card">
          <span className="tv-stat-label">Всего задач</span>
          <span className="tv-stat-value">{stats.total}</span>
        </div>
        <div className="tv-stat-card is-amber">
          <span className="tv-stat-label">В работе</span>
          <span className="tv-stat-value">{stats.inProgress}</span>
        </div>
        <div className="tv-stat-card is-emerald">
          <span className="tv-stat-label">Выполнено</span>
          <span className="tv-stat-value">{stats.completed}</span>
        </div>
        <div className="tv-stat-card is-indigo">
          <span className="tv-stat-label">Возвращено</span>
          <span className="tv-stat-value">{stats.overdue}</span>
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
                placeholder="Поиск по всем задачам (Ctrl+K)"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  setAllTasksPage(1);
                }}
              />
            </div>
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
                  className="tv-chip"
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

      {/* Drawer */}
      {drawerTask && (
        <TaskDrawer
          task={drawerTask}
          onClose={() => setDrawerTask(null)}
          actionLoadingKey={actionLoadingKey}
          getActionButtons={getActionButtons}
          openCompleteModal={openCompleteModal}
          openStatusModal={openStatusModal}
          updateStatus={updateStatus}
          downloadAttachment={downloadAttachment}
          onEditTask={openEditModal}
          onDeleteTask={openDeleteModal}
          onCopyTaskLink={copyTaskLink}
          onToggleChecklistItem={toggleChecklistItem}
          onSaveChecklistNote={saveChecklistNote}
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
        <div className="tv-modal-overlay" onClick={() => setCreateOpen(false)}>
          <div className="tv-modal" onClick={e => e.stopPropagation()}>
            <div className="tv-modal-header">
              <h3 className="tv-modal-title">Новая задача</h3>
              <button className="tv-close-btn" onClick={() => setCreateOpen(false)}><CloseIcon /></button>
            </div>
            <form onSubmit={handleCreate}>
              <div className="tv-modal-body">
                <div className="tv-form-grid">
                  <div className="tv-form-field">
                    <label>Тема *</label>
                    <input className="tv-input" value={form.subject} maxLength={255} disabled={isCreateLoading}
                      placeholder="Введите тему задачи"
                      autoFocus
                      onChange={e => setForm(p => ({ ...p, subject: e.target.value }))} />
                  </div>
                  <div className="tv-form-field">
                    <label>Описание</label>
                    <textarea className="tv-textarea" value={form.description} disabled={isCreateLoading}
                      placeholder="Опишите задачу (необязательно)"
                      onChange={e => setForm(p => ({ ...p, description: e.target.value }))} />
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div className="tv-form-field">
                      <label>Тег</label>
                      <select className="tv-select" value={form.tag} disabled={isCreateLoading}
                        onChange={e => setForm(p => ({ ...p, tag: e.target.value }))}>
                        {TAG_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                    </div>
                    <div className="tv-form-field">
                      <label>Исполнитель *</label>
                      <select className="tv-select" value={form.assignedTo}
                        disabled={isCreateLoading || isRecipientsLoading}
                        onChange={e => setForm(p => ({ ...p, assignedTo: e.target.value }))}>
                        <option value="">{isRecipientsLoading ? 'Загрузка...' : 'Выберите сотрудника'}</option>
                        {recipients.map(r => (
                          <option key={r.id} value={r.id}>
                            {r.name} ({ROLE_LABELS[r.role] || r.role})
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 12 }}>
                    <div className="tv-form-field">
                      <label>Срочность</label>
                      <select className="tv-select" value={form.priority} disabled={isCreateLoading}
                        onChange={e => setForm(p => ({ ...p, priority: e.target.value }))}>
                        {PRIORITY_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                    </div>
                    <div className="tv-form-field">
                      <label>Дедлайн через</label>
                      <div className="tv-form-inline-grid">
                        <input className="tv-input" type="number" min="0" placeholder="Дней" value={form.deadlineDays} disabled={isCreateLoading}
                          onChange={e => setForm(p => ({ ...p, deadlineDays: e.target.value }))} />
                        <input className="tv-input" type="number" min="0" max="23" placeholder="Часов" value={form.deadlineHours} disabled={isCreateLoading}
                          onChange={e => setForm(p => ({ ...p, deadlineHours: e.target.value }))} />
                        <input className="tv-input" type="number" min="0" max="59" placeholder="Минут" value={form.deadlineMinutes} disabled={isCreateLoading}
                          onChange={e => setForm(p => ({ ...p, deadlineMinutes: e.target.value }))} />
                      </div>
                    </div>
                  </div>
                  <div className="tv-soft-block">
                    <label className="tv-form-switch">
                      <input type="checkbox" checked={form.isRegulation} disabled={isCreateLoading}
                        onChange={e => setForm(p => ({
                          ...p,
                          isRegulation: e.target.checked,
                          recurrenceType: e.target.checked ? (p.recurrenceType || 'daily') : ''
                        }))} />
                      Повторяемая регламентная задача
                    </label>
                    {form.isRegulation && (
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px', gap: 10, marginTop: 10 }}>
                        <div className="tv-form-field" style={{ margin: 0 }}>
                          <label>Период</label>
                          <select className="tv-select" value={form.recurrenceType || 'daily'} disabled={isCreateLoading}
                            onChange={e => setForm(p => ({ ...p, recurrenceType: e.target.value }))}>
                            {RECURRENCE_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                          </select>
                        </div>
                        <div className="tv-form-field" style={{ margin: 0 }}>
                          <label>Интервал</label>
                          <input className="tv-input" type="number" min="1" max="365" value={form.recurrenceInterval} disabled={isCreateLoading}
                            onChange={e => setForm(p => ({ ...p, recurrenceInterval: e.target.value }))} />
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="tv-form-field">
                    <label>Чек-лист</label>
                    <ChecklistDraftEditor
                      items={form.checklistItems}
                      disabled={isCreateLoading}
                      onChange={(next) => setForm((p) => ({ ...p, checklistItems: next }))}
                    />
                  </div>
                  <div className="tv-form-field">
                    <label>Прикрепить файлы</label>
                    <input ref={fileInputRef} type="file" multiple className="tv-input" disabled={isCreateLoading}
                      onChange={e => setSelectedFiles(Array.from(e.target.files || []))} />
                    {selectedFiles.length > 0 && (
                      <p style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 4 }}>
                        Прикреплено файлов: {selectedFiles.length}
                      </p>
                    )}
                  </div>
                </div>
              </div>
              <div className="tv-modal-footer">
                <button type="button" className="tv-btn tv-btn-ghost" disabled={isCreateLoading}
                  onClick={() => setCreateOpen(false)}>Отмена</button>
                <button type="submit" className="tv-btn tv-btn-primary"
                  disabled={isCreateLoading || isRecipientsLoading}>
                  {isCreateLoading ? 'Создаю...' : 'Поставить задачу'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editModal.open && (
        <div className="tv-modal-overlay" onClick={closeEditModal}>
          <div className="tv-modal" onClick={e => e.stopPropagation()}>
            <div className="tv-modal-header">
              <h3 className="tv-modal-title">Редактирование задачи</h3>
              <button className="tv-close-btn" onClick={closeEditModal}><CloseIcon /></button>
            </div>
            <form onSubmit={submitEditTask}>
              <div className="tv-modal-body">
                {editModal.taskSubject && (
                  <div style={{
                    background: '#f8f7f4', border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)', padding: '10px 14px',
                    fontSize: 13, color: 'var(--ink-2)', marginBottom: 16,
                    display: 'flex', gap: 8, alignItems: 'flex-start',
                  }}>
                    <span style={{ color: 'var(--ink-3)', marginTop: 1 }}>📌</span>
                    <span><strong style={{ color: 'var(--ink)' }}>{editModal.taskSubject}</strong></span>
                  </div>
                )}
                <div className="tv-form-grid">
                  <div className="tv-form-field">
                    <label>Тема *</label>
                    <input
                      className="tv-input"
                      value={editForm.subject}
                      maxLength={255}
                      disabled={!!actionLoadingKey}
                      autoFocus
                      onChange={e => setEditForm(p => ({ ...p, subject: e.target.value }))}
                    />
                  </div>
                  <div className="tv-form-field">
                    <label>Описание</label>
                    <textarea
                      className="tv-textarea"
                      value={editForm.description}
                      disabled={!!actionLoadingKey}
                      onChange={e => setEditForm(p => ({ ...p, description: e.target.value }))}
                    />
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div className="tv-form-field">
                      <label>Тег</label>
                      <select
                        className="tv-select"
                        value={editForm.tag}
                        disabled={!!actionLoadingKey}
                        onChange={e => setEditForm(p => ({ ...p, tag: e.target.value }))}
                      >
                        {TAG_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                    </div>
                    <div className="tv-form-field">
                      <label>Исполнитель *</label>
                      <select
                        className="tv-select"
                        value={editForm.assignedTo}
                        disabled={!!actionLoadingKey || isRecipientsLoading}
                        onChange={e => setEditForm(p => ({ ...p, assignedTo: e.target.value }))}
                      >
                        <option value="">{isRecipientsLoading ? 'Загрузка...' : 'Выберите сотрудника'}</option>
                        {recipients.map(r => (
                          <option key={r.id} value={r.id}>
                            {r.name} ({ROLE_LABELS[r.role] || r.role})
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 12 }}>
                    <div className="tv-form-field">
                      <label>Срочность</label>
                      <select
                        className="tv-select"
                        value={editForm.priority}
                        disabled={!!actionLoadingKey}
                        onChange={e => setEditForm(p => ({ ...p, priority: e.target.value }))}
                      >
                        {PRIORITY_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                    </div>
                    <div className="tv-form-field">
                      <label>Дедлайн через</label>
                      <div className="tv-form-inline-grid">
                        <input className="tv-input" type="number" min="0" placeholder="Дней" value={editForm.deadlineDays} disabled={!!actionLoadingKey}
                          onChange={e => setEditForm(p => ({ ...p, deadlineDays: e.target.value }))} />
                        <input className="tv-input" type="number" min="0" max="23" placeholder="Часов" value={editForm.deadlineHours} disabled={!!actionLoadingKey}
                          onChange={e => setEditForm(p => ({ ...p, deadlineHours: e.target.value }))} />
                        <input className="tv-input" type="number" min="0" max="59" placeholder="Минут" value={editForm.deadlineMinutes} disabled={!!actionLoadingKey}
                          onChange={e => setEditForm(p => ({ ...p, deadlineMinutes: e.target.value }))} />
                      </div>
                    </div>
                  </div>
                  <div className="tv-soft-block">
                    <label className="tv-form-switch">
                      <input
                        type="checkbox"
                        checked={!!editForm.isRegulation}
                        disabled={!!actionLoadingKey}
                        onChange={e => setEditForm(p => ({
                          ...p,
                          isRegulation: e.target.checked,
                          recurrenceType: e.target.checked ? (p.recurrenceType || 'daily') : ''
                        }))}
                      />
                      Повторяемая регламентная задача
                    </label>
                    {editForm.isRegulation && (
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px', gap: 10, marginTop: 10 }}>
                        <div className="tv-form-field" style={{ margin: 0 }}>
                          <label>Период</label>
                          <select
                            className="tv-select"
                            value={editForm.recurrenceType || 'daily'}
                            disabled={!!actionLoadingKey}
                            onChange={e => setEditForm(p => ({ ...p, recurrenceType: e.target.value }))}
                          >
                            {RECURRENCE_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                          </select>
                        </div>
                        <div className="tv-form-field" style={{ margin: 0 }}>
                          <label>Интервал</label>
                          <input
                            className="tv-input"
                            type="number"
                            min="1"
                            max="365"
                            value={editForm.recurrenceInterval}
                            disabled={!!actionLoadingKey}
                            onChange={e => setEditForm(p => ({ ...p, recurrenceInterval: e.target.value }))}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="tv-form-field">
                    <label>Чек-лист</label>
                    <ChecklistDraftEditor
                      items={editForm.checklistItems}
                      disabled={!!actionLoadingKey}
                      onChange={(next) => setEditForm((p) => ({ ...p, checklistItems: next }))}
                    />
                  </div>
                </div>
              </div>
              <div className="tv-modal-footer">
                <button
                  type="button"
                  className="tv-btn tv-btn-ghost"
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
        <div className="tv-modal-overlay" onClick={closeDeleteModal}>
          <div className="tv-modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 500 }}>
            <div className="tv-modal-header">
              <h3 className="tv-modal-title">Подтверждение удаления</h3>
              <button className="tv-close-btn" type="button" onClick={closeDeleteModal} disabled={!!actionLoadingKey}>
                <CloseIcon />
              </button>
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
                  className="tv-btn tv-btn-ghost"
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
        <div className="tv-modal-overlay" onClick={closeCompleteModal}>
          <div className="tv-modal" onClick={e => e.stopPropagation()}>
            <div className="tv-modal-header">
              <h3 className="tv-modal-title">Завершение задачи</h3>
              <button className="tv-close-btn" onClick={closeCompleteModal}><CloseIcon /></button>
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
                    <label>Итоги выполнения</label>
                    <textarea className="tv-textarea" value={completionSummary}
                      placeholder="Опишите, что сделано по задаче"
                      style={{ minHeight: 110 }}
                      autoFocus
                      disabled={!!actionLoadingKey}
                      onChange={e => setCompletionSummary(e.target.value)} />
                  </div>
                  <div className="tv-form-field">
                    <label>Итоговые файлы</label>
                    <input ref={completionFileInputRef} type="file" multiple className="tv-input"
                      disabled={!!actionLoadingKey}
                      onChange={e => setCompletionFiles(Array.from(e.target.files || []))} />
                    {completionFiles.length > 0 && (
                      <p style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 4 }}>
                        Прикреплено файлов: {completionFiles.length}
                      </p>
                    )}
                  </div>
                </div>
              </div>
              <div className="tv-modal-footer">
                <button type="button" className="tv-btn tv-btn-ghost" disabled={!!actionLoadingKey}
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
        <div className="tv-modal-overlay" onClick={closeStatusModal}>
          <div className="tv-modal" onClick={e => e.stopPropagation()}>
            <div className="tv-modal-header">
              <h3 className="tv-modal-title">{statusModalTitle}</h3>
              <button className="tv-close-btn" onClick={closeStatusModal}><CloseIcon /></button>
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
                    <input ref={statusFileInputRef} type="file" multiple className="tv-input"
                      disabled={!!actionLoadingKey}
                      onChange={e => setStatusFiles(Array.from(e.target.files || []))} />
                    {statusFiles.length > 0 && (
                      <p style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 4 }}>
                        Прикреплено файлов: {statusFiles.length}
                      </p>
                    )}
                  </div>
                </div>
              </div>
              <div className="tv-modal-footer">
                <button type="button" className="tv-btn tv-btn-ghost" disabled={!!actionLoadingKey}
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
  prev.externalRefreshToken === next.externalRefreshToken;

export default React.memo(TasksView, areEqual);
