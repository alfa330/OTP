import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { RefreshCw } from 'lucide-react';
import { normalizeRole, isAdminLikeRole, isSupervisorRole } from '../../utils/roles';

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
    margin: 9px 2px 5px;
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
  .tv-task-row-indicator {
    width: 3px; height: 28px; border-radius: 99px; flex-shrink: 0;
  }
  .tv-task-row-subject {
    flex: 1; font-size: 13.5px; font-weight: 500; color: var(--ink);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
  }
  .tv-task-row-meta { display: flex; align-items: center; gap: 7px; flex-shrink: 0; }
  .tv-task-row-assignee-chip {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 12px; color: var(--ink-2);
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
  .tv-task-row-date { font-size: 11.5px; color: var(--ink-3); }
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
    padding: 2px 9px; border-radius: 99px;
    font-size: 11px; font-weight: 500;
    border: 1px solid transparent;
    white-space: nowrap; line-height: 1.6;
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

  .tv-close-btn {
    width: 32px; height: 32px; border-radius: 8px; flex-shrink: 0;
    background: var(--bg); border: 1px solid var(--border); cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    color: var(--ink-2); transition: all .15s; margin-top: 1px;
  }
  .tv-close-btn:hover { background: var(--border-strong); color: var(--ink); border-color: var(--border-strong); }

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

  .tv-completion-block {
    background: #f0f4ff; border: 1px solid #c7d2fe;
    border-radius: var(--radius-sm); padding: 14px;
  }
  .tv-completion-block .tv-block-label { color: var(--indigo); }

  .tv-history-list { display: flex; flex-direction: column; }
  .tv-history-item {
    display: flex; align-items: baseline; gap: 8px;
    padding: 9px 0; border-bottom: 1px solid var(--border);
    font-size: 12.5px; flex-wrap: wrap;
  }
  .tv-history-item:last-child { border-bottom: none; }
  .tv-history-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--border-strong); flex-shrink: 0; margin-top: 5px; }
  .tv-history-status { font-weight: 500; color: var(--ink); }
  .tv-history-time   { color: var(--ink-3); font-size: 11.5px; }
  .tv-history-who    { color: var(--ink-2); font-style: italic; }
  .tv-history-comment{ color: var(--ink-2); }

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

  @media (max-width: 680px) {
    .tv-root { padding: 16px 12px; }
    .tv-stats-strip { grid-template-columns: repeat(2, 1fr); }
    .tv-drawer { width: 100vw; }
    .tv-info-grid { grid-template-columns: 1fr; }
    .tv-task-row-assignee-chip, .tv-task-row-date { display: none; }
    .tv-pagination { flex-wrap: wrap; justify-content: center; }
    .tv-person-row { flex-wrap: wrap; }
    .tv-person-stats { white-space: normal; gap: 8px; }
    .tv-participants { flex-direction: column; gap: 10px; }
  }
  @media (max-width: 400px) {
    .tv-stats-strip { grid-template-columns: 1fr 1fr; gap: 8px; }
    .tv-stat-value { font-size: 22px; }
    .tv-topbar-title { font-size: 18px; }
  }
`;
document.head.appendChild(styleTag);

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

const fmt = (v) => {
  if (!v) return '—';
  const d = new Date(v);
  return isNaN(d) ? String(v) : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
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
  const nowDay = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const itemDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = Math.round((nowDay.getTime() - itemDay.getTime()) / 86400000);
  if (diff === 0) return 'Сегодня';
  if (diff === 1) return 'Вчера';
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' });
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

/* ─── TaskRow — defined outside to avoid remount ─── */
const TaskRow = React.memo(({ task, onClick }) => {
  const sm = STATUS_META[task.status] || { label: task.status, badge: 'tv-badge-gray', dot: '#ccc' };
  const tm = TAG_META[task.tag]       || { label: task.tag || '—', badge: 'tv-badge-gray' };
  const assigneeName = task?.assignee?.name || '—';
  const assigneeAvatarUrl = task?.assignee?.avatar_url || '';
  return (
    <div className="tv-task-row" onClick={() => onClick(task)}>
      <span className="tv-task-row-indicator" style={{ background: sm.dot }} />
      <span className="tv-task-row-subject">{task.subject || 'Без темы'}</span>
      <span className="tv-task-row-meta">
        <span className={`tv-badge ${tm.badge}`}>{tm.label}</span>
        <span className={`tv-badge ${sm.badge}`}>{sm.label}</span>
        <span className="tv-task-row-assignee-chip">
          <AvatarCircle className="tv-avatar-xs" name={assigneeName} avatarUrl={assigneeAvatarUrl} />
          {assigneeName}
        </span>
        <span className="tv-task-row-date">{fmt(task.created_at)}</span>
        <ChevronRight />
      </span>
    </div>
  );
});

/* ─── TaskDrawer — defined outside to avoid remount ─── */
const TaskDrawer = React.memo(({
  task, onClose, actionLoadingKey,
  getActionButtons, openCompleteModal, openStatusModal, updateStatus, downloadAttachment,
}) => {
  const sm = STATUS_META[task.status] || { label: task.status, badge: 'tv-badge-gray' };
  const tm = TAG_META[task.tag]       || { label: task.tag || '—', badge: 'tv-badge-gray' };
  const attachments     = Array.isArray(task.attachments)            ? task.attachments            : [];
  const compAttachments = Array.isArray(task.completion_attachments) ? task.completion_attachments : [];
  const history         = Array.isArray(task.history)                ? task.history                : [];
  const btns            = getActionButtons(task);

  // ESC key handler
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

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
            </div>
          </div>
          <button className="tv-close-btn" onClick={onClose} aria-label="Закрыть (Esc)">
            <CloseIcon />
          </button>
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
                    <div key={i} className="tv-history-item">
                      <span className="tv-history-dot" />
                      <span className="tv-history-status">{HISTORY_LABELS[item.status_code] || item.status_code}</span>
                      <span className="tv-history-time">{fmt(item.changed_at)}</span>
                      {item.changed_by_name && <span className="tv-history-who">{item.changed_by_name}</span>}
                      {item.comment && <span className="tv-history-comment">— {item.comment}</span>}
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        {btns.length > 0 && (
          <div className="tv-drawer-footer">
            <span style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--ink-3)' }}>
              <span className="tv-kbd">Esc</span> закрыть
            </span>
            {btns.map(btn => {
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

/* ─── Skeleton loading ─── */
const SkeletonList = ({ count = 4 }) => (
  <div className="tv-task-list">
    {Array.from({ length: count }).map((_, i) => (
      <div key={i} className="tv-skeleton tv-skeleton-row" style={{ opacity: 1 - i * 0.15 }} />
    ))}
  </div>
);

/* ─── Main Component ─── */
const TasksView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader }) => {
  const currentUserRole = normalizeRole(user?.role);
  const canAccessTasks = isAdminLikeRole(currentUserRole) || isSupervisorRole(currentUserRole);
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
  const [allTasksPage,        setAllTasksPage]        = useState(1);
  const [allTasksTotal,       setAllTasksTotal]       = useState(0);
  const [filteredTasksTotal,  setFilteredTasksTotal]  = useState(0);
  const [personTasks,         setPersonTasks]         = useState([]);
  const [personTasksPage,     setPersonTasksPage]     = useState(1);
  const [personTasksTotal,    setPersonTasksTotal]    = useState(0);
  const [isPersonTasksLoading, setIsPersonTasksLoading] = useState(false);

  const [createOpen,        setCreateOpen]        = useState(false);
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
  const [form, setForm] = useState({ subject: '', description: '', tag: 'task', assignedTo: '' });

  const showToastRef = useRef(showToast);
  useEffect(() => { showToastRef.current = showToast; }, [showToast]);

  const notify = useCallback((msg, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(msg, type);
  }, []);

  const buildHeaders = useCallback(() => {
    const h = {};
    if (user?.id)     h['X-User-Id'] = String(user.id);
    return typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(h) : h;
  }, [user?.id, withAccessTokenHeader]);

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
    filterTag
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
    personTasksPage
  ]);

  const refreshTasksData = useCallback(async () => {
    const jobs = [fetchTasks(), fetchPagedTasks()];
    if (isPersonDrilldown) jobs.push(fetchPersonTasks());
    await Promise.all(jobs);
  }, [fetchTasks, fetchPagedTasks, fetchPersonTasks, isPersonDrilldown]);

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
    body.append('subject',     form.subject.trim());
    body.append('description', form.description.trim());
    body.append('tag',         form.tag);
    body.append('assigned_to', String(form.assignedTo));
    selectedFiles.forEach(f => body.append('files', f));

    setIsCreateLoading(true);
    try {
      const res = await axios.post(`${apiBaseUrl}/api/tasks`, body, { headers: buildHeaders() });
      notify(res?.data?.message || 'Задача создана');
      if (res?.data?.warning) notify(res.data.warning, 'error');
      setForm({ subject: '', description: '', tag: 'task', assignedTo: '' });
      setSelectedFiles([]);
      setCreateOpen(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
      await refreshTasksData();
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось создать задачу', 'error');
    } finally { setIsCreateLoading(false); }
  };

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
    const assigneeId = Number(task?.assignee?.id || 0);
    const creatorId  = Number(task?.creator?.id  || 0);
    const isAssignee = assigneeId === currentUserId;
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
    return btns;
  }, [currentUserId, currentUserRole]);

  const isReturnAction = statusModal.action === 'returned';
  const statusModalTitle = isReturnAction ? 'Возврат на доработку' : 'Возобновление задачи';
  const statusModalCommentLabel = isReturnAction ? 'Комментарий по доработке' : 'Комментарий к возобновлению';
  const statusModalSubmitLabel = isReturnAction ? 'Вернуть на доработку' : 'Возобновить задачу';
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
          return <TaskRow key={row.key} task={row.task} onClick={setDrawerTask} />;
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

  const hasActiveFilters = Boolean(searchQuery.trim() || filterStatus || filterTag);
  const isAnyTasksLoading = isTasksLoading || isPagedTasksLoading || isPersonTasksLoading;

  return (
    <div className="tv-root">
      {/* Top bar */}
      <div className="tv-topbar">
        <h1 className="tv-topbar-title">Задачи</h1>
        <div className="tv-topbar-actions">
          <button className="tv-btn tv-btn-ghost" onClick={refreshTasksData} disabled={isAnyTasksLoading}>
            <RefreshCw size={13} strokeWidth={2} style={{ transition: 'transform .4s', transform: isAnyTasksLoading ? 'rotate(360deg)' : 'none' }} />
            {isAnyTasksLoading ? 'Обновляю...' : 'Обновить'}
          </button>
          <button className="tv-btn tv-btn-primary" onClick={() => setCreateOpen(true)}>
            <PlusIcon /> Новая задача
          </button>
        </div>
      </div>

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
              {hasActiveFilters && (
                <button
                  type="button"
                  className="tv-chip"
                  style={{ color: 'var(--rose)', borderColor: '#fecdd3' }}
                  onClick={() => {
                    setSearchQuery('');
                    setFilterStatus('');
                    setFilterTag('');
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
  prev.withAccessTokenHeader === next.withAccessTokenHeader;

export default React.memo(TasksView, areEqual);
