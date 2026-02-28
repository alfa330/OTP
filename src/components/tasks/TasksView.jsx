import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';

/* ─── Google Fonts ─── */
const fontLink = document.createElement('link');
fontLink.rel = 'stylesheet';
fontLink.href = 'https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;1,9..40,300&display=swap';
document.head.appendChild(fontLink);

/* ─── Injected styles ─── */
const styleTag = document.createElement('style');
styleTag.textContent = `
  .tv-root * { font-family: 'DM Sans', sans-serif; box-sizing: border-box; }
  .tv-root h1, .tv-root h2, .tv-root .heading { font-family: 'Syne', sans-serif; }

  .tv-root {
    --bg: #f7f6f3;
    --surface: #ffffff;
    --border: #e8e5df;
    --border-strong: #d0ccc3;
    --ink: #1a1916;
    --ink-2: #5c5852;
    --ink-3: #9e9a93;
    --accent: #1a1916;
    --accent-fg: #ffffff;
    --blue: #2563eb;
    --amber: #d97706;
    --indigo: #4338ca;
    --emerald: #059669;
    --rose: #e11d48;
    --shadow-sm: 0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
    --shadow-md: 0 4px 16px rgba(0,0,0,.08), 0 2px 6px rgba(0,0,0,.05);
    --shadow-lg: 0 20px 60px rgba(0,0,0,.14), 0 8px 24px rgba(0,0,0,.08);
    --radius: 12px;
    --radius-sm: 8px;
    background: var(--bg);
    min-height: 100vh;
    padding: 32px 24px;
  }

  /* ── Section ── */
  .tv-section { margin-bottom: 40px; }
  .tv-section-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 16px;
  }
  .tv-section-title {
    font-family: 'Syne', sans-serif;
    font-size: 13px; font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase;
    color: var(--ink-3);
  }

  /* ── Buttons ── */
  .tv-btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 7px 14px; border-radius: var(--radius-sm);
    font-size: 13px; font-weight: 500;
    border: 1px solid transparent;
    cursor: pointer; transition: all .15s ease;
    white-space: nowrap;
  }
  .tv-btn:disabled { opacity: .5; cursor: not-allowed; }
  .tv-btn-ghost {
    background: transparent;
    border-color: var(--border-strong);
    color: var(--ink-2);
  }
  .tv-btn-ghost:hover:not(:disabled) {
    background: var(--surface);
    color: var(--ink);
    border-color: var(--ink-3);
  }
  .tv-btn-primary {
    background: var(--accent); color: var(--accent-fg);
    border-color: var(--accent);
  }
  .tv-btn-primary:hover:not(:disabled) { background: #333; }
  .tv-btn-blue   { background: var(--blue); color: #fff; }
  .tv-btn-blue:hover:not(:disabled) { background: #1d4ed8; }
  .tv-btn-amber  { background: #fef3c7; color: var(--amber); border-color: #fde68a; }
  .tv-btn-amber:hover:not(:disabled) { background: #fde68a; }
  .tv-btn-indigo { background: #eef2ff; color: var(--indigo); border-color: #c7d2fe; }
  .tv-btn-indigo:hover:not(:disabled) { background: #e0e7ff; }
  .tv-btn-emerald{ background: #d1fae5; color: var(--emerald); border-color: #a7f3d0; }
  .tv-btn-emerald:hover:not(:disabled){ background: #a7f3d0; }
  .tv-btn-rose   { background: #ffe4e6; color: var(--rose); border-color: #fecdd3; }
  .tv-btn-rose:hover:not(:disabled){ background: #fecdd3; }

  /* ── Task Row ── */
  .tv-task-list { display: flex; flex-direction: column; gap: 2px; }
  .tv-task-row {
    display: flex; align-items: center; gap: 12px;
    padding: 13px 16px;
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
  .tv-task-row-dot {
    width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
  }
  .tv-task-row-subject {
    flex: 1; font-size: 14px; font-weight: 500; color: var(--ink);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .tv-task-row-meta {
    font-size: 12px; color: var(--ink-3); flex-shrink: 0;
    display: flex; align-items: center; gap: 10px;
  }
  .tv-badge {
    display: inline-flex; align-items: center;
    padding: 2px 9px; border-radius: 99px;
    font-size: 11px; font-weight: 500;
    border: 1px solid transparent;
    white-space: nowrap;
  }
  .tv-badge-gray  { background: #f1f0ed; color: var(--ink-2); border-color: var(--border); }
  .tv-badge-blue  { background: #dbeafe; color: #1e40af; border-color: #bfdbfe; }
  .tv-badge-amber { background: #fef3c7; color: #92400e; border-color: #fde68a; }
  .tv-badge-indigo{ background: #eef2ff; color: #3730a3; border-color: #c7d2fe; }
  .tv-badge-emerald{background: #d1fae5; color: #065f46; border-color: #a7f3d0; }
  .tv-badge-rose  { background: #ffe4e6; color: #9f1239; border-color: #fecdd3; }

  /* ── Empty / Loading ── */
  .tv-empty {
    padding: 32px; text-align: center;
    color: var(--ink-3); font-size: 13px;
    background: var(--surface); border: 1px dashed var(--border);
    border-radius: var(--radius);
  }
  .tv-loading {
    padding: 24px; text-align: center;
    color: var(--ink-3); font-size: 13px;
  }

  /* ── Drawer overlay ── */
  .tv-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.3);
    backdrop-filter: blur(2px);
    z-index: 40;
    animation: tvFadeIn .2s ease;
  }
  @keyframes tvFadeIn { from { opacity: 0 } to { opacity: 1 } }

  .tv-drawer {
    position: fixed; top: 0; right: 0; bottom: 0;
    width: min(560px, 100vw);
    background: var(--surface);
    z-index: 50;
    display: flex; flex-direction: column;
    box-shadow: var(--shadow-lg);
    animation: tvSlideIn .22s cubic-bezier(.22,1,.36,1);
    overflow: hidden;
  }
  @keyframes tvSlideIn {
    from { transform: translateX(100%) }
    to   { transform: translateX(0) }
  }
  .tv-drawer-header {
    padding: 24px 24px 20px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: flex-start; gap: 12px;
    flex-shrink: 0;
  }
  .tv-drawer-header-text { flex: 1; min-width: 0; }
  .tv-drawer-title {
    font-family: 'Syne', sans-serif;
    font-size: 17px; font-weight: 600; color: var(--ink);
    margin: 0 0 6px;
    word-break: break-word;
  }
  .tv-drawer-badges { display: flex; flex-wrap: wrap; gap: 5px; }
  .tv-drawer-close {
    width: 32px; height: 32px; border-radius: 8px; flex-shrink: 0;
    background: #f1f0ed; border: none; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    color: var(--ink-2); transition: all .15s;
    font-size: 14px; margin-top: 2px;
  }
  .tv-drawer-close:hover { background: var(--border-strong); color: var(--ink); }

  .tv-drawer-body {
    flex: 1; overflow-y: auto; padding: 24px;
    display: flex; flex-direction: column; gap: 20px;
  }
  .tv-drawer-footer {
    padding: 16px 24px;
    border-top: 1px solid var(--border);
    display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end;
    flex-shrink: 0;
    background: var(--bg);
  }

  /* ── Detail blocks ── */
  .tv-info-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
  }
  .tv-info-item label {
    display: block; font-size: 11px; font-weight: 600;
    letter-spacing: .06em; text-transform: uppercase;
    color: var(--ink-3); margin-bottom: 3px;
  }
  .tv-info-item span {
    font-size: 13px; color: var(--ink); font-weight: 400;
  }
  .tv-divider {
    height: 1px; background: var(--border); margin: 0;
  }
  .tv-description {
    font-size: 14px; color: var(--ink-2); line-height: 1.65;
    white-space: pre-wrap; word-break: break-word;
  }
  .tv-block-label {
    font-size: 11px; font-weight: 600;
    letter-spacing: .06em; text-transform: uppercase;
    color: var(--ink-3); margin-bottom: 8px;
  }
  .tv-file-list { display: flex; flex-wrap: wrap; gap: 6px; }
  .tv-file-btn {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 6px 12px; border-radius: var(--radius-sm);
    background: #f1f0ed; border: 1px solid var(--border);
    color: var(--ink-2); font-size: 12px;
    cursor: pointer; transition: all .12s;
  }
  .tv-file-btn:hover { background: var(--border); color: var(--ink); }

  .tv-completion-block {
    background: #f0f4ff; border: 1px solid #c7d2fe;
    border-radius: var(--radius-sm); padding: 14px;
  }
  .tv-completion-block .tv-block-label { color: var(--indigo); }

  .tv-history-list { display: flex; flex-direction: column; gap: 0; }
  .tv-history-item {
    display: flex; align-items: baseline; gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
  }
  .tv-history-item:last-child { border-bottom: none; }
  .tv-history-status { font-weight: 500; color: var(--ink); }
  .tv-history-time { color: var(--ink-3); }
  .tv-history-who { color: var(--ink-2); font-style: italic; }
  .tv-history-comment { color: var(--ink-2); }

  /* ── Modal (create / complete) ── */
  .tv-modal-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.35);
    backdrop-filter: blur(3px);
    z-index: 60; display: flex; align-items: center; justify-content: center;
    padding: 16px;
    animation: tvFadeIn .18s ease;
  }
  .tv-modal {
    background: var(--surface);
    width: 100%; max-width: 580px;
    border-radius: var(--radius);
    box-shadow: var(--shadow-lg);
    overflow: hidden;
    animation: tvScaleIn .2s cubic-bezier(.22,1,.36,1);
    max-height: 90vh;
    display: flex; flex-direction: column;
  }
  @keyframes tvScaleIn {
    from { transform: scale(.95); opacity: 0 }
    to   { transform: scale(1); opacity: 1 }
  }
  .tv-modal-header {
    padding: 20px 24px 18px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    flex-shrink: 0;
  }
  .tv-modal-title {
    font-family: 'Syne', sans-serif;
    font-size: 16px; font-weight: 600; color: var(--ink); margin: 0;
  }
  .tv-modal-body {
    padding: 20px 24px; overflow-y: auto;
    flex: 1;
  }
  .tv-modal-footer {
    padding: 14px 24px;
    border-top: 1px solid var(--border);
    display: flex; justify-content: flex-end; gap: 8px;
    background: var(--bg); flex-shrink: 0;
  }

  /* ── Form ── */
  .tv-form-grid { display: flex; flex-direction: column; gap: 16px; }
  .tv-form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .tv-form-field label {
    display: block; font-size: 12px; font-weight: 500;
    color: var(--ink-2); margin-bottom: 5px;
  }
  .tv-input, .tv-textarea, .tv-select {
    width: 100%; padding: 9px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-size: 13px; color: var(--ink);
    background: var(--bg);
    outline: none; transition: border .15s, box-shadow .15s;
    font-family: 'DM Sans', sans-serif;
  }
  .tv-input:focus, .tv-textarea:focus, .tv-select:focus {
    border-color: var(--ink-3);
    box-shadow: 0 0 0 3px rgba(26,25,22,.06);
  }
  .tv-input:disabled, .tv-textarea:disabled, .tv-select:disabled {
    opacity: .55; cursor: not-allowed;
  }
  .tv-textarea { resize: vertical; min-height: 90px; line-height: 1.5; }

  @media (max-width: 600px) {
    .tv-root { padding: 16px 12px; }
    .tv-drawer { width: 100vw; }
    .tv-info-grid, .tv-form-row { grid-template-columns: 1fr; }
    .tv-task-row-meta { display: none; }
  }
`;
document.head.appendChild(styleTag);

/* ─── Constants ─── */
const TAG_OPTIONS = [
  { value: 'task', label: 'Задача' },
  { value: 'problem', label: 'Проблема' },
  { value: 'suggestion', label: 'Предложение' },
];

const STATUS_META = {
  assigned:    { label: 'Выставлен',        badge: 'tv-badge-blue',    dot: '#93c5fd' },
  in_progress: { label: 'В работе',          badge: 'tv-badge-amber',   dot: '#fcd34d' },
  completed:   { label: 'Выполнен',          badge: 'tv-badge-indigo',  dot: '#a5b4fc' },
  accepted:    { label: 'Принят',            badge: 'tv-badge-emerald', dot: '#6ee7b7' },
  returned:    { label: 'Возвращён',         badge: 'tv-badge-rose',    dot: '#fda4af' },
};

const HISTORY_LABELS = {
  assigned:    'Выставлен',
  in_progress: 'Принят в работу',
  completed:   'Выполнен',
  accepted:    'Принят',
  returned:    'Возвращён на доработку',
  reopened:    'Возобновлён',
};

const TAG_LABELS = Object.fromEntries(TAG_OPTIONS.map((t) => [t.value, t.label]));
const ROLE_LABELS = { admin: 'Админ', sv: 'СВ' };

const fmt = (v) => {
  if (!v) return '—';
  const d = new Date(v);
  return isNaN(d) ? String(v) : d.toLocaleString('ru-RU');
};

/* ─── Small helpers ─── */
const CloseIcon = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
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
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
    <path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);
const RefreshIcon = () => (
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
    <path d="M13.5 2.5v4h-4M2.5 13.5v-4h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    <path d="M13.5 6.5A6 6 0 102.5 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
  </svg>
);
const PlusIcon = () => (
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
    <path d="M8 2v12M2 8h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
  </svg>
);

/* ─────────────────────────────────── Main Component ─── */
const TasksView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader }) => {
  const [tasks, setTasks] = useState([]);
  const [recipients, setRecipients] = useState([]);
  const [isTasksLoading, setIsTasksLoading] = useState(false);
  const [isRecipientsLoading, setIsRecipientsLoading] = useState(false);
  const [isCreateLoading, setIsCreateLoading] = useState(false);
  const [actionLoadingKey, setActionLoadingKey] = useState('');
  const [selectedFiles, setSelectedFiles] = useState([]);

  // Modals
  const [createOpen, setCreateOpen] = useState(false);
  const [drawerTask, setDrawerTask] = useState(null);
  const [completeModal, setCompleteModal] = useState({ open: false, taskId: null, taskSubject: '' });
  const [completionSummary, setCompletionSummary] = useState('');
  const [completionFiles, setCompletionFiles] = useState([]);

  const fileInputRef = useRef(null);
  const completionFileInputRef = useRef(null);

  const [form, setForm] = useState({ subject: '', description: '', tag: 'task', assignedTo: '' });

  const showToastRef = useRef(showToast);
  useEffect(() => { showToastRef.current = showToast; }, [showToast]);

  const notify = useCallback((msg, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(msg, type);
  }, []);

  const buildHeaders = useCallback(() => {
    const h = {};
    if (user?.id) h['X-User-Id'] = String(user.id);
    if (user?.apiKey) h['X-API-Key'] = user.apiKey;
    return typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(h) : h;
  }, [user?.id, user?.apiKey, withAccessTokenHeader]);

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
      const res = await axios.get(`${apiBaseUrl}/api/tasks`, { headers: buildHeaders() });
      const list = Array.isArray(res?.data?.tasks) ? res.data.tasks : [];
      setTasks(list);
      // Sync drawer task if open
      setDrawerTask(prev => prev ? (list.find(t => t.id === prev.id) || prev) : null);
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось загрузить задачи', 'error');
    } finally { setIsTasksLoading(false); }
  }, [apiBaseUrl, buildHeaders, notify]);

  useEffect(() => {
    if (!user || !['admin', 'sv'].includes(user.role)) return;
    fetchRecipients();
    fetchTasks();
  }, [user, fetchRecipients, fetchTasks]);

  const currentUserId = Number(user?.id || 0);
  const myTasks = useMemo(
    () => tasks.filter(t => Number(t?.assignee?.id || 0) === currentUserId),
    [tasks, currentUserId]
  );

  /* ── Create task ── */
  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.subject.trim()) { notify('Укажите тему задачи', 'error'); return; }
    if (!form.assignedTo) { notify('Выберите сотрудника', 'error'); return; }

    const body = new FormData();
    body.append('subject', form.subject.trim());
    body.append('description', form.description.trim());
    body.append('tag', form.tag);
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
      await fetchTasks();
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось создать задачу', 'error');
    } finally { setIsCreateLoading(false); }
  };

  /* ── Update status ── */
  const updateStatus = async (taskId, action) => {
    const comment = action === 'returned'
      ? (window.prompt('Комментарий по доработке (необязательно):', '') || '').trim()
      : '';
    const key = `${taskId}:${action}`;
    setActionLoadingKey(key);
    try {
      const res = await axios.post(
        `${apiBaseUrl}/api/tasks/${taskId}/status`,
        { action, comment },
        { headers: buildHeaders() }
      );
      notify(res?.data?.message || 'Статус обновлён');
      await fetchTasks();
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось обновить статус', 'error');
    } finally { setActionLoadingKey(''); }
  };

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
      closeCompleteModal();
      await fetchTasks();
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось завершить задачу', 'error');
    } finally { setActionLoadingKey(''); }
  }, [completeModal, completionSummary, completionFiles, apiBaseUrl, buildHeaders, notify, closeCompleteModal, fetchTasks]);

  /* ── Download ── */
  const downloadAttachment = async (att) => {
    try {
      const res = await axios.get(
        `${apiBaseUrl}/api/tasks/attachments/${att.id}/download`,
        { headers: buildHeaders(), responseType: 'blob' }
      );
      const blob = new Blob([res.data], { type: att.content_type || 'application/octet-stream' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = att.file_name || `attachment-${att.id}`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      notify(e?.response?.data?.error || 'Не удалось скачать файл', 'error');
    }
  };

  /* ── Action buttons for a task ── */
  const getActionButtons = (task) => {
    const assigneeId = Number(task?.assignee?.id || 0);
    const creatorId  = Number(task?.creator?.id || 0);
    const isAssignee = assigneeId === currentUserId;
    const canReview  = !isAssignee && (user?.role === 'admin' || creatorId === currentUserId || user?.role === 'sv');
    const s = task?.status;
    const btns = [];
    if (isAssignee && (s === 'assigned' || s === 'returned'))
      btns.push({ action: 'in_progress', label: 'Принять в работу', cls: 'tv-btn-amber' });
    if (isAssignee && (s === 'in_progress' || s === 'returned'))
      btns.push({ action: 'completed', label: 'Выполнить', cls: 'tv-btn-indigo' });
    if (canReview && s === 'completed') {
      btns.push({ action: 'accepted', label: 'Принять', cls: 'tv-btn-emerald' });
      btns.push({ action: 'returned', label: 'Вернуть', cls: 'tv-btn-rose' });
    }
    if (canReview && s === 'accepted')
      btns.push({ action: 'reopened', label: 'Возобновить', cls: 'tv-btn-ghost' });
    return btns;
  };

  /* ── Task Row ── */
  const TaskRow = ({ task }) => {
    const sm = STATUS_META[task.status] || { label: task.status, badge: 'tv-badge-gray', dot: '#ccc' };
    return (
      <div className="tv-task-row" onClick={() => setDrawerTask(task)}>
        <span className="tv-task-row-dot" style={{ background: sm.dot }} />
        <span className="tv-task-row-subject">{task.subject || 'Без темы'}</span>
        <span className="tv-task-row-meta">
          <span className={`tv-badge ${sm.badge}`}>{sm.label}</span>
          <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>{task?.assignee?.name || '—'}</span>
          <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>{fmt(task.created_at)}</span>
          <ChevronRight />
        </span>
      </div>
    );
  };

  /* ── Task Drawer ── */
  const TaskDrawer = ({ task }) => {
    if (!task) return null;
    const sm = STATUS_META[task.status] || { label: task.status, badge: 'tv-badge-gray', dot: '#ccc' };
    const attachments = Array.isArray(task.attachments) ? task.attachments : [];
    const compAttachments = Array.isArray(task.completion_attachments) ? task.completion_attachments : [];
    const history = Array.isArray(task.history) ? task.history : [];
    const btns = getActionButtons(task);

    return (
      <>
        <div className="tv-overlay" onClick={() => setDrawerTask(null)} />
        <div className="tv-drawer">
          <div className="tv-drawer-header">
            <div className="tv-drawer-header-text">
              <h2 className="tv-drawer-title">{task.subject || 'Без темы'}</h2>
              <div className="tv-drawer-badges">
                <span className={`tv-badge ${sm.badge}`}>{sm.label}</span>
                <span className="tv-badge tv-badge-gray">{TAG_LABELS[task.tag] || task.tag || '—'}</span>
              </div>
            </div>
            <button className="tv-drawer-close" onClick={() => setDrawerTask(null)} aria-label="Закрыть">
              <CloseIcon />
            </button>
          </div>

          <div className="tv-drawer-body">
            {/* Meta grid */}
            <div className="tv-info-grid">
              <div className="tv-info-item">
                <label>Исполнитель</label>
                <span>{task?.assignee?.name || '—'}</span>
              </div>
              <div className="tv-info-item">
                <label>Постановщик</label>
                <span>{task?.creator?.name || '—'}</span>
              </div>
              <div className="tv-info-item">
                <label>Создано</label>
                <span>{fmt(task.created_at)}</span>
              </div>
              <div className="tv-info-item">
                <label>Статус</label>
                <span>{sm.label}</span>
              </div>
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
                        <button key={att.id} className="tv-file-btn" onClick={() => downloadAttachment(att)}
                          style={{ background: '#e0e7ff', borderColor: '#c7d2fe', color: '#3730a3' }}>
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
                        <span className="tv-history-status">
                          {HISTORY_LABELS[item.status_code] || item.status_code}
                        </span>
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
              {btns.map(btn => {
                const key = `${task.id}:${btn.action}`;
                const loading = actionLoadingKey === key;
                return (
                  <button
                    key={btn.action}
                    className={`tv-btn ${btn.cls}`}
                    disabled={!!actionLoadingKey}
                    onClick={() => {
                      if (btn.action === 'completed') { openCompleteModal(task); return; }
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
  };

  /* ── Task list render ── */
  const renderList = (list, emptyText) => {
    if (isTasksLoading) return <div className="tv-loading">Загрузка...</div>;
    if (!list.length) return <div className="tv-empty">{emptyText}</div>;
    return (
      <div className="tv-task-list">
        {list.map(t => <TaskRow key={t.id} task={t} />)}
      </div>
    );
  };

  if (!user || !['admin', 'sv'].includes(user.role)) return null;

  return (
    <div className="tv-root">
      {/* My tasks */}
      <div className="tv-section">
        <div className="tv-section-header">
          <span className="tv-section-title heading">Мои задачи</span>
          <button className="tv-btn tv-btn-ghost" onClick={fetchTasks} disabled={isTasksLoading}>
            <RefreshIcon />{isTasksLoading ? 'Обновляю...' : 'Обновить'}
          </button>
        </div>
        {renderList(myTasks, 'У вас пока нет задач.')}
      </div>

      {/* All tasks */}
      <div className="tv-section">
        <div className="tv-section-header">
          <span className="tv-section-title heading">Все задачи</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="tv-btn tv-btn-ghost" onClick={fetchTasks} disabled={isTasksLoading}>
              <RefreshIcon />{isTasksLoading ? 'Обновляю...' : 'Обновить'}
            </button>
            <button className="tv-btn tv-btn-primary" onClick={() => setCreateOpen(true)}>
              <PlusIcon />Добавить задачу
            </button>
          </div>
        </div>
        {renderList(tasks, 'Пока задач нет.')}
      </div>

      {/* Task Drawer */}
      {drawerTask && <TaskDrawer task={drawerTask} />}

      {/* Create Modal */}
      {createOpen && (
        <div className="tv-modal-overlay" onClick={() => setCreateOpen(false)}>
          <div className="tv-modal" onClick={e => e.stopPropagation()}>
            <div className="tv-modal-header">
              <h3 className="tv-modal-title">Новая задача</h3>
              <button className="tv-drawer-close" onClick={() => setCreateOpen(false)}><CloseIcon /></button>
            </div>
            <form onSubmit={handleCreate}>
              <div className="tv-modal-body">
                <div className="tv-form-grid">
                  <div className="tv-form-field" style={{ gridColumn: '1 / -1' }}>
                    <label>Тема</label>
                    <input className="tv-input" value={form.subject} maxLength={255} disabled={isCreateLoading}
                      placeholder="Введите тему задачи"
                      onChange={e => setForm(p => ({ ...p, subject: e.target.value }))} />
                  </div>
                  <div className="tv-form-field" style={{ gridColumn: '1 / -1' }}>
                    <label>Описание</label>
                    <textarea className="tv-textarea" value={form.description} disabled={isCreateLoading}
                      placeholder="Опишите задачу (необязательно)"
                      onChange={e => setForm(p => ({ ...p, description: e.target.value }))} />
                  </div>
                  <div className="tv-form-field">
                    <label>Тег</label>
                    <select className="tv-select" value={form.tag} disabled={isCreateLoading}
                      onChange={e => setForm(p => ({ ...p, tag: e.target.value }))}>
                      {TAG_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  </div>
                  <div className="tv-form-field">
                    <label>Исполнитель</label>
                    <select className="tv-select" value={form.assignedTo}
                      disabled={isCreateLoading || isRecipientsLoading}
                      onChange={e => setForm(p => ({ ...p, assignedTo: e.target.value }))}>
                      <option value="">Выберите сотрудника</option>
                      {recipients.map(r => (
                        <option key={r.id} value={r.id}>
                          {r.name} ({ROLE_LABELS[r.role] || r.role})
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="tv-form-field" style={{ gridColumn: '1 / -1' }}>
                    <label>Файлы</label>
                    <input ref={fileInputRef} type="file" multiple className="tv-input" disabled={isCreateLoading}
                      onChange={e => setSelectedFiles(Array.from(e.target.files || []))} />
                    {selectedFiles.length > 0 && (
                      <p style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 4 }}>
                        Прикреплено: {selectedFiles.length}
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
              <button className="tv-drawer-close" onClick={closeCompleteModal}><CloseIcon /></button>
            </div>
            <form onSubmit={submitComplete}>
              <div className="tv-modal-body">
                {completeModal.taskSubject && (
                  <p style={{ fontSize: 13, color: 'var(--ink-2)', marginBottom: 16 }}>
                    <strong style={{ color: 'var(--ink)' }}>Задача:</strong> {completeModal.taskSubject}
                  </p>
                )}
                <div className="tv-form-grid">
                  <div className="tv-form-field">
                    <label>Итоги выполнения</label>
                    <textarea className="tv-textarea" value={completionSummary}
                      placeholder="Опишите, что сделано по задаче"
                      style={{ minHeight: 110 }}
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
                        Прикреплено: {completionFiles.length}
                      </p>
                    )}
                  </div>
                </div>
              </div>
              <div className="tv-modal-footer">
                <button type="button" className="tv-btn tv-btn-ghost" disabled={!!actionLoadingKey}
                  onClick={closeCompleteModal}>Отмена</button>
                <button type="submit" className="tv-btn tv-btn-indigo" disabled={!!actionLoadingKey}>
                  {actionLoadingKey === `${completeModal.taskId}:completed`
                    ? 'Сохраняю...' : 'Отметить выполненной'}
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