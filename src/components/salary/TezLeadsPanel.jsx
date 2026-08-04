import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import InfoHint from '../common/InfoHint';

/**
 * База лидов TEZ ОП и статистика успешек.
 *
 * База помесячная и накопительная внутри месяца: один номер, загруженный дважды
 * за июнь — это один лид с upload_count=2, а тот же номер в июльской базе — уже
 * другой лид. Успешка датируется днём первой поездки водителя, поэтому звонок
 * из последних 7 дней июня даёт успешку июля — при поездке любым днём июля.
 *
 * Props:
 *  - apiBaseUrl: базовый URL API
 *  - userId: id текущего пользователя (заголовок X-User-Id)
 *  - departmentId: id отдела
 *  - groupId: id выбранной группы (сужает рейтинг операторов и разбивку по дням;
 *             воронка и загрузки остаются на уровне отдела — база лидов общая)
 *  - month: 'YYYY-MM'
 *  - canEdit: можно ли загружать, удалять/восстанавливать базу и запускать сверку
 *  - onDataChanged: уведомление после удаления/восстановления для внешних витрин
 */

const STATUS_LABELS = {
  new: 'Новый',
  in_progress: 'В работе',
  already_working: 'Уже работающий',
  success: 'Успешка',
  not_counted: 'Не засчитана',
};

const STATUS_STYLES = {
  new: 'bg-gray-100 text-gray-700',
  in_progress: 'bg-blue-100 text-blue-700',
  already_working: 'bg-amber-100 text-amber-800',
  success: 'bg-emerald-100 text-emerald-700',
  not_counted: 'bg-rose-100 text-rose-700',
};

const RULE_LABELS = {
  same_month: 'Звонок в месяце поездки',
  prev_month_last7: 'Звонок в последние 7 дней прошлого месяца',
  no_call_before_trip: 'Нет звонка до поездки',
  call_before_last7: 'Звонок раньше последних 7 дней прошлого месяца',
  active_prev_month: 'Были заказы в прошлом месяце — уже работал',
  // Коды прежнего правила (окно было на стороне поездки): новый расчёт их не
  // выдаёт, но на закрытых месяцах они остались в БД.
  prev_month_week1: 'Звонок в прошлом месяце, поездка до 7-го (старое правило)',
  trip_after_day7: 'Поездка позже 7-го числа (старое правило)',
};

const TEZ_LEADS_MAX_FILES_PER_UPLOAD = 10;

const ALMATY_DATE_TIME_FORMATTER = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Almaty',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

const fmtDateTime = (value) => {
  if (!value) return '—';
  const raw = String(value);
  // Старые значения в БД встречаются без offset. Их нельзя трактовать как время
  // браузера: сохраняем прежнее отображение «как записано». Новые aware timestamps
  // приводим к единой бизнес-зоне Алматы.
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw)) {
    return raw.replace('T', ' ').slice(0, 16);
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return raw.replace('T', ' ').slice(0, 16);
  }
  const parts = Object.fromEntries(
    ALMATY_DATE_TIME_FORMATTER.formatToParts(parsed)
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value])
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
};

const LeadTripDates = ({ row }) => {
  const previousTrip = row?.prev_month_first_order_at;
  // first_order_at остаётся fallback для старого backend-контракта во время
  // поэтапного деплоя; новое имя однозначно означает отчётный месяц.
  const currentTrip = row?.month_first_order_at || row?.first_order_at;
  if (!previousTrip && !currentTrip) return <span>—</span>;

  const previousCausedStatus = (
    row?.status === 'already_working' && row?.status_rule === 'active_prev_month'
  );

  return (
    <div className="space-y-1 whitespace-nowrap">
      {previousTrip && (
        <div className={previousCausedStatus ? 'text-amber-700' : 'text-gray-500'}>
          <div className="text-[10px] font-semibold uppercase tracking-wide">
            Предыдущий месяц{previousCausedStatus ? ' · причина статуса' : ''}
          </div>
          <div className={previousCausedStatus ? 'font-medium' : ''}>
            {fmtDateTime(previousTrip)}
          </div>
        </div>
      )}
      {currentTrip && (
        <div className="text-gray-500">
          <div className="text-[10px] font-semibold uppercase tracking-wide">
            Отчётный месяц
          </div>
          <div>{fmtDateTime(currentTrip)}</div>
        </div>
      )}
    </div>
  );
};

/**
 * Подтверждение удаления/отката загрузки. Причину спрашиваем только при удалении:
 * она уходит в журнал и потом видна в строке загрузки, поэтому «почему убрали базу»
 * не приходится восстанавливать по памяти.
 */
const BatchActionDialog = ({
  mode,
  batch,
  busy,
  disabled,
  reason,
  error,
  onReason,
  onCancel,
  onConfirm,
}) => {
  const dialogRef = useRef(null);
  const reasonRef = useRef(null);
  const removing = mode === 'delete';
  const reasonInvalid = removing && !reason.trim();

  useEffect(() => {
    if (!batch) return undefined;
    const previousFocus = document.activeElement;
    const target = removing
      ? reasonRef.current
      : dialogRef.current?.querySelector('[data-primary-action]');
    target?.focus();
    return () => {
      if (
        typeof HTMLElement !== 'undefined' &&
        previousFocus instanceof HTMLElement &&
        document.contains(previousFocus)
      ) {
        previousFocus.focus();
      }
    };
  }, [batch, removing]);

  // FullscreenSheet тоже слушает Escape на document. Перехватываем событие в
  // capture-фазе, чтобы Escape закрыл только подтверждение, а не весь экран.
  useEffect(() => {
    if (!batch) return undefined;
    const handleEscape = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!busy) onCancel();
    };
    document.addEventListener('keydown', handleEscape, true);
    return () => document.removeEventListener('keydown', handleEscape, true);
  }, [batch, busy, onCancel]);

  if (!batch) return null;

  const trapFocus = (event) => {
    if (event.key !== 'Tab') return;
    const focusable = Array.from(
      dialogRef.current?.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      ) || []
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 p-4">
      <form
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tez-batch-action-title"
        aria-describedby={`tez-batch-action-description${error ? ' tez-batch-action-error' : ''}`}
        aria-busy={busy}
        onKeyDown={trapFocus}
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && !disabled && !reasonInvalid) onConfirm();
        }}
        className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl"
      >
        <h3
          id="tez-batch-action-title"
          className={`flex items-center gap-2 text-base font-semibold ${removing ? 'text-rose-700' : 'text-emerald-700'}`}
        >
          <FaIcon
            className={`fas ${removing ? 'fa-trash-can' : 'fa-rotate-left'}`}
            aria-hidden="true"
          />
          {removing ? 'Удалить загруженную базу?' : 'Восстановить базу?'}
        </h3>
        <p className="mt-2 text-sm text-slate-600">
          Файл <b className="break-all">{batch.file_name || '—'}</b> ({batch.rows_total} строк,
          загрузил {batch.uploaded_by_name || '—'} {fmtDateTime(batch.created_at)}).
        </p>
        <p
          id="tez-batch-action-description"
          className={`mt-2 rounded-xl border px-3 py-2 text-xs ${
            removing
              ? 'border-amber-200 bg-amber-50 text-amber-800'
              : 'border-emerald-200 bg-emerald-50 text-emerald-800'
          }`}
        >
          {removing ? (
            <>
              Лиды, пришедшие только из этой загрузки, уйдут из базы вместе с их успешками —
              воронка и рейтинг операторов пересчитаются. Лиды, которые есть и в других
              загрузках, останутся. Действие <b>обратимо</b>: загрузка сохранится в истории
              с кнопкой «Восстановить».
            </>
          ) : (
            <>
              Лиды и успешки вернутся из архива. Если за это время тот же номер завела
              другая загрузка, лид не задвоится — строки подклеятся к существующему.
              Для таких слияний актуальный результат восстановит кнопка «Сверить сейчас».
            </>
          )}
        </p>
        {removing && (
          <label className="mt-3 block">
            <span className="text-xs font-medium text-slate-600">Причина (попадёт в историю)</span>
            <input
              ref={reasonRef}
              value={reason}
              onChange={(e) => onReason(e.target.value)}
              placeholder="например: загрузили не тот файл"
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              maxLength={2000}
              required
              aria-invalid={reasonInvalid}
              aria-describedby="tez-batch-reason-hint"
            />
            <span id="tez-batch-reason-hint" className="mt-1 flex justify-between gap-2 text-[11px] text-slate-500">
              <span>Обязательное поле</span>
              <span>{reason.length}/2000</span>
            </span>
          </label>
        )}
        {error && (
          <div
            id="tez-batch-action-error"
            role="alert"
            className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800"
          >
            {error}
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Отмена
          </button>
          <button
            type="submit"
            data-primary-action
            disabled={busy || disabled || reasonInvalid}
            className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold text-white disabled:opacity-60 ${
              removing ? 'bg-rose-600 hover:bg-rose-700' : 'bg-emerald-600 hover:bg-emerald-700'
            }`}
          >
            <FaIcon
              className={`fas ${busy ? 'fa-spinner fa-spin' : removing ? 'fa-trash-can' : 'fa-rotate-left'}`}
              aria-hidden="true"
            />
            {busy ? (removing ? 'Удаляем…' : 'Восстанавливаем…') : removing ? 'Удалить' : 'Восстановить'}
          </button>
        </div>
      </form>
    </div>
  );
};

const TezLeadsPanel = ({
  apiBaseUrl = '',
  userId,
  departmentId,
  groupId = null,
  month,
  canEdit = false,
  onDataChanged,
}) => {
  const [stats, setStats] = useState(null);
  const [leads, setLeads] = useState([]);
  const [tab, setTab] = useState('operators');
  const [statusFilter, setStatusFilter] = useState('');
  const [operatorFilter, setOperatorFilter] = useState('');
  const [search, setSearch] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [uploadResults, setUploadResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [msg, setMsg] = useState('');
  const [checkMsg, setCheckMsg] = useState('');
  const [invalidRows, setInvalidRows] = useState([]);
  const [page, setPage] = useState(1);
  const [leadsTotal, setLeadsTotal] = useState(0);
  const [leadsPages, setLeadsPages] = useState(1);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState('');
  const [statsLoadedScope, setStatsLoadedScope] = useState('');
  const [batchAction, setBatchAction] = useState(null); // {mode, batch, scopeKey}
  const [batchReason, setBatchReason] = useState('');
  const [batchActionError, setBatchActionError] = useState('');
  const [batchBusy, setBatchBusy] = useState(false);
  const [showDeleted, setShowDeleted] = useState(true);
  const [historyState, setHistoryState] = useState({
    batchId: null,
    status: 'idle',
    events: [],
    error: '',
  });
  const fileRef = useRef(null);
  const pollRef = useRef(null);
  const pollGenerationRef = useRef(0);
  const pollBatchIdsRef = useRef(new Set());
  const statsRequestRef = useRef(0);
  const leadsRequestRef = useRef(0);
  const historyRequestRef = useRef(0);
  const activeScopeRef = useRef('');

  const PAGE_SIZE = 50;

  const [year, monthNum] = String(month || '').split('-').map((v) => parseInt(v, 10));
  const validPeriod = Number.isFinite(year) && Number.isFinite(monthNum);
  const headers = useMemo(() => ({ 'X-User-Id': userId }), [userId]);
  const statsScopeKey = validPeriod && userId
    ? [apiBaseUrl, userId, departmentId ?? '', groupId ?? '', year, monthNum].join('|')
    : '';
  activeScopeRef.current = statsScopeKey;

  const loadStats = useCallback(() => {
    if (statsScopeKey !== activeScopeRef.current) return Promise.resolve(null);
    const requestId = ++statsRequestRef.current;
    if (!statsScopeKey) {
      setStats(null);
      setStatsLoadedScope('');
      setStatsLoading(false);
      return Promise.resolve(null);
    }
    setStatsLoading(true);
    setStatsError('');
    return axios
      .get(`${apiBaseUrl}/api/tez_leads/stats`, {
        params: { year, month: monthNum, group_id: groupId || undefined },
        headers,
      })
      .then((resp) => {
        if (
          requestId !== statsRequestRef.current ||
          statsScopeKey !== activeScopeRef.current
        ) return null;
        const data = resp?.data || null;
        setStats(data);
        setStatsLoadedScope(data ? statsScopeKey : '');
        return data;
      })
      .catch((err) => {
        if (
          requestId !== statsRequestRef.current ||
          statsScopeKey !== activeScopeRef.current
        ) return null;
        setStats(null);
        setStatsLoadedScope('');
        setStatsError(err?.response?.data?.error || 'Не удалось загрузить данные базы');
        return null;
      })
      .finally(() => {
        if (
          requestId === statsRequestRef.current &&
          statsScopeKey === activeScopeRef.current
        ) setStatsLoading(false);
      });
  }, [apiBaseUrl, headers, year, monthNum, groupId, statsScopeKey]);

  const loadLeads = useCallback((toPage = 1) => {
    const requestId = ++leadsRequestRef.current;
    const requestScope = statsScopeKey;
    if (!validPeriod || !userId || !requestScope) return Promise.resolve(null);
    return axios
      .get(`${apiBaseUrl}/api/tez_leads/detail`, {
        params: {
          year, month: monthNum,
          status: statusFilter || undefined,
          operator_id: operatorFilter || undefined,
          search: search || undefined,
          page: toPage, page_size: PAGE_SIZE,
        },
        headers,
      })
      .then((resp) => {
        if (
          requestId !== leadsRequestRef.current ||
          requestScope !== activeScopeRef.current
        ) return null;
        const d = resp?.data || {};
        setLeads(d.leads || []);
        setLeadsTotal(d.total || 0);
        setLeadsPages(d.pages || 1);
        setPage(d.page || toPage);
        return d;
      })
      .catch(() => {
        if (
          requestId !== leadsRequestRef.current ||
          requestScope !== activeScopeRef.current
        ) return null;
        setLeads([]);
        setLeadsTotal(0);
        setLeadsPages(1);
        return null;
      });
  }, [
    apiBaseUrl,
    headers,
    userId,
    year,
    monthNum,
    validPeriod,
    statusFilter,
    operatorFilter,
    search,
    statsScopeKey,
  ]);

  useEffect(() => {
    // Смена месяца/группы/пользователя мгновенно инвалидирует старые данные и
    // любые открытые действия. Поздние ответы прежнего scope игнорируются по id.
    statsRequestRef.current += 1;
    leadsRequestRef.current += 1;
    historyRequestRef.current += 1;
    pollGenerationRef.current += 1;
    pollBatchIdsRef.current.clear();
    clearTimeout(pollRef.current);
    setStats(null);
    setStatsLoadedScope('');
    setStatsError('');
    setMsg('');
    setCheckMsg('');
    setBatchAction(null);
    setBatchReason('');
    setBatchActionError('');
    setBatchBusy(false);
    setHistoryState({ batchId: null, status: 'idle', events: [], error: '' });
    setUploadResults([]);
    setUploadProgress(null);
    setInvalidRows([]);
    setLeads([]);
    setLeadsTotal(0);
    setLeadsPages(1);
    setPage(1);
    loadStats();
    return () => {
      statsRequestRef.current += 1;
      leadsRequestRef.current += 1;
      historyRequestRef.current += 1;
      pollGenerationRef.current += 1;
      pollBatchIdsRef.current.clear();
      clearTimeout(pollRef.current);
    };
  }, [loadStats, statsScopeKey]);

  useEffect(() => {
    if (!canEdit) {
      setBatchAction(null);
      setBatchReason('');
      setBatchActionError('');
    }
  }, [canEdit]);

  // Смена вкладки/фильтров/поиска — всегда с первой страницы.
  useEffect(() => {
    if (tab === 'leads') loadLeads(1);
  }, [tab, loadLeads]);

  // Проверки загруженных баз идут в фоне. Один таймер следит сразу за всеми
  // созданными batch, чтобы несколько параллельных poll не перетирали друг друга.
  const pollBatches = useCallback((
    batchIds,
    attempt = 0,
    scopeKey = statsScopeKey,
    generation = pollGenerationRef.current,
  ) => {
    const ids = [...new Set((batchIds || []).filter(Boolean))];
    if (
      !ids.length ||
      scopeKey !== activeScopeRef.current ||
      generation !== pollGenerationRef.current
    ) return;
    if (attempt > 60) {
      pollBatchIdsRef.current.clear();
      setCheckMsg('Проверка загруженных баз продолжается в фоне. Статусы видны в списке загрузок.');
      return;
    }
    clearTimeout(pollRef.current);
    pollRef.current = setTimeout(() => {
      if (
        scopeKey !== activeScopeRef.current ||
        generation !== pollGenerationRef.current
      ) return;
      loadStats().then((data) => {
        if (
          scopeKey !== activeScopeRef.current ||
          generation !== pollGenerationRef.current
        ) return;
        const batchesById = new Map(
          (data?.batches || []).map((batch) => [batch.id, batch])
        );
        const pending = ids.filter((id) => {
          const batch = batchesById.get(id);
          return !batch || batch.check_status === 'pending' || batch.check_status === 'running';
        });
        if (pending.length) {
          pollBatches(ids, attempt + 1, scopeKey, generation);
          return;
        }

        const failed = ids
          .map((id) => batchesById.get(id))
          .filter((batch) => batch?.check_status === 'error');
        pollBatchIdsRef.current.clear();
        if (failed.length) {
          setCheckMsg(`Проверка завершена с ошибками: ${failed.length} из ${ids.length}. Подробности — в списке загрузок.`);
        } else if (ids.length === 1) {
          const batch = batchesById.get(ids[0]);
          setCheckMsg(`Проверка базы завершена: уже работающих — ${batch?.already_working ?? 0}`);
        } else {
          setCheckMsg(`Проверка завершена для всех загруженных файлов: ${ids.length}.`);
        }
      });
    }, 3000);
  }, [loadStats, statsScopeKey]);

  useEffect(() => () => {
    pollGenerationRef.current += 1;
    pollBatchIdsRef.current.clear();
    clearTimeout(pollRef.current);
  }, []);

  const upload = useCallback(async () => {
    const files = Array.from(fileRef.current?.files || []);
    if (!files.length || !validPeriod) return;
    if (files.length > TEZ_LEADS_MAX_FILES_PER_UPLOAD) {
      setUploadResults([]);
      setInvalidRows([]);
      setMsg(`За один раз можно загрузить не более ${TEZ_LEADS_MAX_FILES_PER_UPLOAD} файлов.`);
      return;
    }

    const uploadScope = statsScopeKey;
    setUploading(true);
    setUploadProgress({ current: 0, total: files.length, fileName: '' });
    setUploadResults([]);
    setMsg('');
    if (!pollBatchIdsRef.current.size) setCheckMsg('');
    setInvalidRows([]);
    const results = [];
    const invalid = [];
    const batchIds = [];

    try {
      for (let index = 0; index < files.length; index += 1) {
        if (uploadScope !== activeScopeRef.current) return;
        const file = files[index];
        setUploadProgress({
          current: index + 1,
          total: files.length,
          fileName: file.name,
        });

        const form = new FormData();
        form.append('file', file);
        form.append('year', year);
        form.append('month', monthNum);
        if (departmentId) form.append('department_id', departmentId);

        try {
          const resp = await axios.post(
            `${apiBaseUrl}/api/tez_leads/upload`,
            form,
            { headers }
          );
          const data = resp?.data || {};
          const result = {
            ok: true,
            fileName: file.name,
            batchId: data.batch_id || null,
            rowsTotal: Number(data.rows_total || 0),
            rowsNew: Number(data.rows_new || 0),
            rowsDuplicate: Number(data.rows_duplicate || 0),
            rowsInvalid: Number(data.rows_invalid || 0),
          };
          results.push(result);
          invalid.push(
            ...(data.invalid_rows || []).map((row) => ({
              ...row,
              source_file_name: file.name,
            }))
          );
          if (result.batchId) batchIds.push(result.batchId);
        } catch (err) {
          results.push({
            ok: false,
            fileName: file.name,
            error: err?.response?.data?.error || 'Не удалось загрузить файл',
          });
        }
        if (uploadScope !== activeScopeRef.current) return;
        setUploadResults([...results]);
      }

      const successful = results.filter((result) => result.ok);
      const failed = results.filter((result) => !result.ok);
      const totals = successful.reduce(
        (acc, result) => ({
          rowsTotal: acc.rowsTotal + result.rowsTotal,
          rowsNew: acc.rowsNew + result.rowsNew,
          rowsDuplicate: acc.rowsDuplicate + result.rowsDuplicate,
          rowsInvalid: acc.rowsInvalid + result.rowsInvalid,
        }),
        { rowsTotal: 0, rowsNew: 0, rowsDuplicate: 0, rowsInvalid: 0 }
      );

      setInvalidRows(invalid);
      if (successful.length) {
        setMsg(
          `Загружено файлов: ${successful.length} из ${files.length}. ` +
          `Строк: ${totals.rowsTotal}, новых: ${totals.rowsNew}, ` +
          `дублей: ${totals.rowsDuplicate}, невалидных: ${totals.rowsInvalid}.` +
          (failed.length ? ` Ошибок: ${failed.length}.` : '')
        );
        await loadStats();
        if (uploadScope !== activeScopeRef.current) return;
        if (tab === 'leads') loadLeads(1);
        batchIds.forEach((batchId) => pollBatchIdsRef.current.add(batchId));
        const activeBatchIds = [...pollBatchIdsRef.current];
        const pollGeneration = ++pollGenerationRef.current;
        clearTimeout(pollRef.current);
        setCheckMsg('Проверка загруженных баз на уже работающих выполняется…');
        pollBatches(activeBatchIds, 0, uploadScope, pollGeneration);
      } else {
        setMsg(`Не удалось загрузить ни одного файла из ${files.length}.`);
      }
    } finally {
      if (fileRef.current) fileRef.current.value = '';
      setUploadProgress(null);
      setUploading(false);
    }
  }, [
    apiBaseUrl,
    headers,
    departmentId,
    year,
    monthNum,
    validPeriod,
    loadStats,
    loadLeads,
    pollBatches,
    statsScopeKey,
    tab,
  ]);

  const recompute = useCallback(() => {
    if (!validPeriod) return;
    setBusy(true);
    setMsg('');
    axios
      .post(`${apiBaseUrl}/api/tez_leads/recompute`, null, { params: { year, month: monthNum }, headers })
      .then((resp) => {
        const o = resp?.data?.outcomes || {};
        setMsg(`Сверка выполнена: успешек ${o.success || 0}, уже работающих ${o.already_working || 0}`);
        loadStats();
        if (tab === 'leads') loadLeads();
      })
      .catch((err) => setMsg(err?.response?.data?.error || 'Не удалось выполнить сверку'))
      .finally(() => setBusy(false));
  }, [apiBaseUrl, headers, year, monthNum, validPeriod, loadStats, loadLeads, tab]);

  const statsIsCurrent = Boolean(stats && statsLoadedScope === statsScopeKey);
  const currentStats = statsIsCurrent ? stats : null;
  const batches = currentStats?.batches || [];
  const batchActionsEnabled = canEdit && statsIsCurrent && !statsLoading && !batchBusy;

  const closeBatchAction = useCallback(() => {
    if (batchBusy) return;
    setBatchAction(null);
    setBatchReason('');
    setBatchActionError('');
  }, [batchBusy]);

  const openBatchAction = useCallback((mode, batch) => {
    const currentBatch = batches.find((item) => String(item.id) === String(batch?.id));
    const expectedDeleted = mode === 'restore';
    if (
      !batchActionsEnabled ||
      !currentBatch ||
      Boolean(currentBatch.is_deleted) !== expectedDeleted
    ) {
      setMsg('Данные загрузок обновляются. Повторите действие после загрузки списка.');
      loadStats();
      return;
    }
    setBatchReason('');
    setBatchActionError('');
    setBatchAction({ mode, batch: currentBatch, scopeKey: statsScopeKey });
  }, [batchActionsEnabled, batches, loadStats, statsScopeKey]);

  // Удаление/откат меняют состав базы, поэтому перечитываем и воронку, и лиды:
  // счётчики в карточках иначе останутся от прежнего состава.
  const confirmBatchAction = useCallback(async () => {
    if (!batchAction) return;
    const { mode, batch, scopeKey: actionScope } = batchAction;
    const reason = batchReason.trim();
    if (mode === 'delete' && !reason) {
      setBatchActionError('Укажите причину удаления.');
      return;
    }
    if (reason.length > 2000) {
      setBatchActionError('Причина не должна быть длиннее 2000 символов.');
      return;
    }
    const currentBatch = batches.find((item) => String(item.id) === String(batch.id));
    const expectedDeleted = mode === 'restore';
    if (
      !canEdit ||
      statsLoading ||
      !statsIsCurrent ||
      actionScope !== statsScopeKey ||
      !currentBatch ||
      Boolean(currentBatch.is_deleted) !== expectedDeleted
    ) {
      setBatchActionError('Состояние загрузки изменилось. Обновите список и повторите действие.');
      loadStats();
      return;
    }

    setBatchBusy(true);
    setBatchActionError('');
    setMsg('');
    try {
      const resp = await axios.post(
        `${apiBaseUrl}/api/tez_leads/batches/${batch.id}/${mode}`,
        { reason: mode === 'delete' ? reason : reason.slice(0, 2000) },
        { headers }
      );
      if (actionScope !== activeScopeRef.current) return;
      const d = resp?.data || {};
      setMsg(
        mode === 'delete'
          ? `База «${batch.file_name}» удалена: лидов убрано ${d.leads_removed ?? 0}, ` +
            `успешек снято ${d.successes_removed ?? 0}, оставлено (есть в других загрузках) ${d.leads_kept ?? 0}. ` +
            'Откатить можно кнопкой «Восстановить».'
          : `База «${batch.file_name}» восстановлена: лидов ${d.leads_restored ?? 0}, ` +
            `успешек ${d.successes_restored ?? 0}` +
            (d.leads_merged ? `, слито с уже существующими ${d.leads_merged}` : '') +
            '. Рекомендуем нажать «Сверить сейчас».'
      );
      historyRequestRef.current += 1;
      setHistoryState({ batchId: null, status: 'idle', events: [], error: '' });
      setBatchAction(null);
      setBatchReason('');
      setBatchActionError('');
      onDataChanged?.();
      loadStats();
      if (tab === 'leads') loadLeads(1);
    } catch (err) {
      if (actionScope !== activeScopeRef.current) return;
      const errorMessage = err?.response?.data?.error || 'Не удалось выполнить операцию';
      if (err?.response?.status === 409) {
        setBatchActionError(`${errorMessage}. Обновляем список…`);
        const refreshed = await loadStats();
        if (actionScope !== activeScopeRef.current) return;
        if (refreshed) {
          setBatchAction(null);
          setBatchReason('');
          setBatchActionError('');
          setMsg(`${errorMessage}. Список загрузок обновлён.`);
        } else {
          setBatchActionError(`${errorMessage}. Не удалось обновить список загрузок.`);
        }
      } else {
        setBatchActionError(errorMessage);
      }
    } finally {
      if (actionScope === activeScopeRef.current) setBatchBusy(false);
    }
  }, [
    apiBaseUrl,
    headers,
    batchAction,
    batchReason,
    batches,
    canEdit,
    statsLoading,
    statsIsCurrent,
    statsScopeKey,
    loadStats,
    loadLeads,
    tab,
    onDataChanged,
  ]);

  const toggleHistory = useCallback(async (batchId, forceReload = false) => {
    if (!statsIsCurrent || statsScopeKey !== activeScopeRef.current) return;
    if (historyState.batchId === batchId && !forceReload) {
      historyRequestRef.current += 1;
      setHistoryState({ batchId: null, status: 'idle', events: [], error: '' });
      return;
    }

    const requestId = ++historyRequestRef.current;
    const requestScope = statsScopeKey;
    setHistoryState({ batchId, status: 'loading', events: [], error: '' });
    try {
      const resp = await axios.get(
        `${apiBaseUrl}/api/tez_leads/batches/${batchId}/history`,
        { headers }
      );
      if (
        requestId !== historyRequestRef.current ||
        requestScope !== activeScopeRef.current
      ) return;
      setHistoryState({
        batchId,
        status: 'loaded',
        events: resp?.data?.events || [],
        error: '',
      });
    } catch (err) {
      if (
        requestId !== historyRequestRef.current ||
        requestScope !== activeScopeRef.current
      ) return;
      setHistoryState({
        batchId,
        status: 'error',
        events: [],
        error: err?.response?.data?.error || 'Не удалось загрузить историю',
      });
    }
  }, [
    apiBaseUrl,
    headers,
    historyState.batchId,
    statsIsCurrent,
    statsScopeKey,
  ]);

  // Экспорт качаем через axios (blob): простой <a href> не несёт токен/куки и
  // упирается в 401, если транспорт авторизации bearer, а не cookie.
  const exportExcel = useCallback(() => {
    if (!validPeriod || exporting) return;
    setExporting(true);
    axios
      .get(`${apiBaseUrl}/api/tez_leads/export`, {
        params: { year, month: monthNum },
        headers,
        responseType: 'blob',
      })
      .then((resp) => {
        const url = window.URL.createObjectURL(new Blob([resp.data]));
        const link = document.createElement('a');
        link.href = url;
        link.download = `tez_leads_${year}_${String(monthNum).padStart(2, '0')}.xlsx`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
      })
      .catch(() => setMsg('Не удалось сформировать выгрузку'))
      .finally(() => setExporting(false));
  }, [apiBaseUrl, headers, year, monthNum, validPeriod, exporting]);

  const funnel = currentStats?.funnel || {};
  const deletedCount = useMemo(() => batches.filter((b) => b.is_deleted).length, [batches]);
  const visibleBatches = useMemo(
    () => (showDeleted ? batches : batches.filter((b) => !b.is_deleted)),
    [batches, showDeleted]
  );
  const selectedActionBatch = batchAction
    ? batches.find((item) => String(item.id) === String(batchAction.batch?.id))
    : null;
  const batchActionDisabled = Boolean(batchAction) && (
    !canEdit ||
    statsLoading ||
    !statsIsCurrent ||
    batchAction.scopeKey !== statsScopeKey ||
    !selectedActionBatch ||
    Boolean(selectedActionBatch.is_deleted) !== (batchAction.mode === 'restore')
  );

  const funnelCards = [
    { label: 'Загружено лидов', value: funnel.leads_total, hint: `дублей при загрузке: ${funnel.duplicates ?? 0}` },
    { label: 'Обзвонено', value: funnel.dialed, hint: 'есть хотя бы один звонок' },
    { label: 'Дозвонились', value: funnel.reached, hint: 'разговор от 10 сек' },
    {
      label: 'Заказ в этом месяце',
      value: funnel.went_online,
      hint: `работали и в прошлом: ${funnel.active_prev_month ?? 0}`,
      info: (
        <>
          Водители, у которых есть заказ в отчётном месяце. Из них
          {' '}<b>{funnel.active_prev_month ?? 0}</b> выполняли заказы и в прошлом месяце —
          значит уже работали, привлечения не было. Всего «уже работающих»
          (вместе с теми, кто выехал без нашего звонка): <b>{funnel.already_working ?? 0}</b>;
          в знаменатель конверсии они не входят.
        </>
      ),
    },
    {
      label: 'Успешки',
      value: funnel.successes,
      hint: `конверсия ${funnel.conversion ?? 0}%`,
      accent: true,
      info: (
        <>
          Конверсия <b>{funnel.conversion ?? 0}%</b> считается от рабочей части базы
          ({funnel.workable ?? 0} лидов, без «уже работающих»). От всей базы было бы
          {' '}{funnel.conversion_all ?? 0}%. Не засчитано по правилу дат: {funnel.not_counted ?? 0}.
        </>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <BatchActionDialog
        mode={batchAction?.mode}
        batch={batchAction?.batch}
        busy={batchBusy}
        disabled={batchActionDisabled}
        reason={batchReason}
        error={batchActionError || (batchAction ? statsError : '')}
        onReason={(value) => {
          setBatchReason(value);
          if (batchActionError) setBatchActionError('');
        }}
        onCancel={closeBatchAction}
        onConfirm={confirmBatchAction}
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        {canEdit ? (
          <div className="flex flex-col sm:flex-row sm:items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              multiple
              disabled={uploading}
              accept=".csv,.xlsx,.xlsm"
              className="text-sm disabled:cursor-wait disabled:opacity-60 file:mr-3 file:px-3 file:py-2 file:rounded-full file:border-0 file:bg-indigo-100 file:text-indigo-700"
            />
            <button
              onClick={upload}
              disabled={uploading || !validPeriod}
              aria-busy={uploading}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-semibold text-white shadow-sm ${
                uploading ? 'bg-indigo-300 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700'
              }`}
            >
              <FaIcon className="fas fa-upload" />
              {uploading
                ? (
                  uploadProgress
                    ? `Загрузка ${uploadProgress.current}/${uploadProgress.total}…`
                    : 'Загрузка…'
                )
                : 'Загрузить базы'}
            </button>
            <span className="inline-flex items-center gap-1 text-xs text-slate-500">
              {uploading && uploadProgress?.fileName
                ? `Сейчас: ${uploadProgress.fileName}`
                : 'Колонки: fio, phone'}
              <InfoHint title="Формат файла" side="left">
                CSV или Excel с колонками <b>fio</b> и <b>phone</b>. Шапка необязательна — тогда
                первая колонка считается именем, вторая телефоном. Телефон в любом формате
                (8700…, +7 700…, 700…) приводится к 11 цифрам. База помесячная: тот же номер,
                загруженный повторно за месяц, не создаёт дубль, а увеличивает счётчик загрузок.
                За один раз можно выбрать до <b>{TEZ_LEADS_MAX_FILES_PER_UPLOAD}</b> файлов;
                каждый файл обрабатывается отдельно и может содержать до 50 000 строк.
              </InfoHint>
            </span>
          </div>
        ) : <span />}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={exportExcel}
            disabled={exporting}
            className={`inline-flex h-9 items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3.5 text-xs font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-100 ${exporting ? 'cursor-wait opacity-60' : ''}`}
          >
            <FaIcon className={`fas ${exporting ? 'fa-spinner fa-spin' : 'fa-file-excel'}`} />
            Excel
          </button>
          {canEdit && (
            <button
              onClick={recompute}
              disabled={busy}
              className={`inline-flex h-9 items-center gap-2 rounded-full border border-slate-200 bg-white px-3.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 ${
                busy ? 'cursor-wait opacity-60' : ''
              }`}
            >
              <FaIcon className={`fas ${busy ? 'fa-spinner fa-spin' : 'fa-rotate'}`} />
              Сверить сейчас
            </button>
          )}
        </div>
      </div>

      {msg && (
        <div
          role="status"
          aria-live="polite"
          className="text-sm font-medium text-indigo-800 bg-indigo-50 border border-indigo-100 rounded-xl px-3 py-2"
        >
          {msg}
        </div>
      )}

      {checkMsg && (
        <div
          role="status"
          aria-live="polite"
          className="text-sm text-sky-800 bg-sky-50 border border-sky-100 rounded-xl px-3 py-2"
        >
          {checkMsg}
        </div>
      )}

      {uploadResults.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-sm">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-semibold text-slate-800">
              Результаты загрузки ({uploadResults.filter((result) => result.ok).length}/{uploadResults.length})
            </div>
            {!uploading && (
              <button
                type="button"
                onClick={() => setUploadResults([])}
                className="text-xs text-slate-500 hover:text-slate-700"
              >
                скрыть
              </button>
            )}
          </div>
          <div className="mt-2 space-y-1.5">
            {uploadResults.map((result, index) => (
              <div
                key={`${result.fileName}-${index}`}
                className={`flex items-start gap-2 rounded-lg px-2.5 py-2 text-xs ${
                  result.ok
                    ? 'bg-emerald-50 text-emerald-800'
                    : 'bg-rose-50 text-rose-800'
                }`}
              >
                <FaIcon
                  className={`fas ${result.ok ? 'fa-circle-check' : 'fa-circle-exclamation'} mt-0.5`}
                  aria-hidden="true"
                />
                <div className="min-w-0">
                  <div className="break-all font-semibold">{result.fileName}</div>
                  <div className="mt-0.5 opacity-90">
                    {result.ok
                      ? `Строк: ${result.rowsTotal}, новых: ${result.rowsNew}, дублей: ${result.rowsDuplicate}, невалидных: ${result.rowsInvalid}`
                      : result.error}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {statsError && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800"
        >
          <span>{statsError}</span>
          <button
            type="button"
            onClick={loadStats}
            disabled={statsLoading}
            className="rounded-full border border-rose-200 bg-white px-3 py-1 text-xs font-semibold hover:bg-rose-100 disabled:opacity-60"
          >
            Повторить
          </button>
        </div>
      )}

      {invalidRows.length > 0 && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-semibold text-rose-800">
              <FaIcon className="fas fa-triangle-exclamation" />
              Невалидные номера — не попали в базу ({invalidRows.length})
            </div>
            <button
              type="button"
              onClick={() => setInvalidRows([])}
              className="text-xs text-rose-600 hover:text-rose-800"
            >
              скрыть
            </button>
          </div>
          <p className="mt-1 text-xs text-rose-700">
            Пустые ячейки или не казахстанский формат (номер не приводится к 11 цифрам 77…).
            Поправьте в файле и загрузите повторно.
          </p>
          <div className="mt-2 max-h-48 overflow-y-auto rounded-lg bg-white/70 border border-rose-100">
            <table className="min-w-full text-xs">
              <thead className="text-rose-500">
                <tr>
                  <th className="text-left px-2 py-1 font-medium">Файл</th>
                  <th className="text-left px-2 py-1 font-medium">Строка</th>
                  <th className="text-left px-2 py-1 font-medium">ФИО</th>
                  <th className="text-left px-2 py-1 font-medium">Номер в файле</th>
                </tr>
              </thead>
              <tbody>
                {invalidRows.map((r, i) => (
                  <tr key={`${r.source_file_name || 'file'}-${r.row || 0}-${i}`} className="border-t border-rose-50">
                    <td className="max-w-48 break-all px-2 py-1 text-slate-500">{r.source_file_name || '—'}</td>
                    <td className="px-2 py-1 text-slate-400 tabular-nums">{r.row}</td>
                    <td className="px-2 py-1">{r.full_name || '—'}</td>
                    <td className="px-2 py-1 font-mono text-rose-700">{r.phone || '(пусто)'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        {funnelCards.map((card) => (
          <div
            key={card.label}
            className={`rounded-2xl px-3 py-2.5 border shadow-sm ${
              card.accent ? 'bg-emerald-50 border-emerald-200' : 'bg-white border-slate-200'
            }`}
          >
            <div className="flex items-center gap-1 text-xs text-slate-500">
              {card.label}
              {card.info && <InfoHint side="left">{card.info}</InfoHint>}
            </div>
            <div className={`text-xl font-bold ${card.accent ? 'text-emerald-700' : 'text-slate-800'}`}>
              {card.value ?? 0}
            </div>
            <div className="text-[11px] text-slate-500">{card.hint}</div>
          </div>
        ))}
      </div>

      {groupId && (
        <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2">
          Выбрана группа: «Операторы» и «По дням» показаны только по ней.
          Воронка и загрузки — по всему отделу, база лидов общая.
        </div>
      )}

      <div className="flex gap-1 mb-3">
        {[
          ['operators', 'Операторы'],
          ['days', 'По дням'],
          ['leads', 'Лиды'],
          ['batches', 'Загрузки'],
        ].map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium ${
              tab === key ? 'bg-indigo-600 text-white' : 'bg-white text-indigo-700 border border-indigo-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="bg-white rounded-lg border border-indigo-100 overflow-x-auto">
        {tab === 'operators' && (
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="text-left px-3 py-2">Оператор</th>
                <th className="text-right px-3 py-2">Успешки</th>
                <th className="text-left px-3 py-2">Первая</th>
                <th className="text-left px-3 py-2">Последняя</th>
              </tr>
            </thead>
            <tbody>
              {(currentStats?.operators || []).map((row) => (
                <tr key={row.operator_id || row.operator_name} className="border-t">
                  <td className="px-3 py-2">{row.operator_name || '—'}</td>
                  <td className="px-3 py-2 text-right font-semibold">{row.successes}</td>
                  <td className="px-3 py-2 text-gray-500">{row.first_success || '—'}</td>
                  <td className="px-3 py-2 text-gray-500">{row.last_success || '—'}</td>
                </tr>
              ))}
              {!(currentStats?.operators || []).length && (
                <tr><td colSpan={4} className="px-3 py-6 text-center text-gray-400">Успешек за месяц пока нет</td></tr>
              )}
            </tbody>
          </table>
        )}

        {tab === 'days' && (
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="text-left px-3 py-2">Дата поездки</th>
                <th className="text-right px-3 py-2">Успешки</th>
              </tr>
            </thead>
            <tbody>
              {(currentStats?.by_day || []).map((row) => (
                <tr key={row.date} className="border-t">
                  <td className="px-3 py-2">{row.date}</td>
                  <td className="px-3 py-2 text-right font-semibold">{row.successes}</td>
                </tr>
              ))}
              {!(currentStats?.by_day || []).length && (
                <tr><td colSpan={2} className="px-3 py-6 text-center text-gray-400">Нет данных</td></tr>
              )}
            </tbody>
          </table>
        )}

        {tab === 'batches' && (
          <div>
            {deletedCount > 0 && (
              <div className="flex items-center justify-between gap-2 border-b bg-gray-50 px-3 py-2">
                <span className="text-xs text-slate-500">
                  Удалённых загрузок: {deletedCount}. Они остаются в истории и восстанавливаются в один клик.
                </span>
                <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs font-medium text-slate-600">
                  <input
                    type="checkbox"
                    checked={showDeleted}
                    onChange={(e) => setShowDeleted(e.target.checked)}
                    className="rounded border-slate-300"
                  />
                  Показывать удалённые
                </label>
              </div>
            )}
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="text-left px-3 py-2">Файл</th>
                  <th className="text-left px-3 py-2">Загрузил</th>
                  <th className="text-right px-3 py-2">Строк</th>
                  <th className="text-right px-3 py-2">Новых</th>
                  <th className="text-right px-3 py-2">Дублей</th>
                  <th className="text-right px-3 py-2">Битых</th>
                  <th className="text-right px-3 py-2">Уже работают</th>
                  <th className="text-left px-3 py-2">Когда</th>
                  <th className="text-right px-3 py-2">
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleBatches.map((b) => (
                  <React.Fragment key={b.id}>
                    <tr className={`border-t ${b.is_deleted ? 'bg-rose-50/50 text-slate-400' : ''}`}>
                      <td className="px-3 py-2">
                        <span className={b.is_deleted ? 'line-through' : ''}>{b.file_name}</span>
                        {b.is_deleted && (
                          <span className="ml-2 rounded bg-rose-100 px-1.5 py-0.5 text-[11px] font-medium text-rose-700 no-underline">
                            удалена
                          </span>
                        )}
                        {!b.is_deleted && b.restored_at && (
                          <span className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700">
                            восстановлена
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">{b.uploaded_by_name || '—'}</td>
                      <td className="px-3 py-2 text-right">{b.rows_total}</td>
                      <td className="px-3 py-2 text-right">{b.rows_new}</td>
                      <td className="px-3 py-2 text-right">{b.rows_duplicate}</td>
                      <td className="px-3 py-2 text-right">{b.rows_invalid}</td>
                      <td className="px-3 py-2 text-right">
                        {b.check_status === 'done' ? b.already_working : (
                          <span className="text-gray-400">
                            {b.check_status === 'error' ? 'ошибка' : 'проверка…'}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-gray-500">{fmtDateTime(b.created_at)}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => toggleHistory(b.id)}
                          title="История удалений и восстановлений"
                          aria-label={`История загрузки ${b.file_name || ''}`}
                          aria-expanded={historyState.batchId === b.id}
                          aria-controls={`tez-batch-history-${b.id}`}
                          className="mr-1 inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                        >
                          <FaIcon className="fas fa-clock-rotate-left" aria-hidden="true" />
                        </button>
                        {canEdit && (b.is_deleted ? (
                          <button
                            type="button"
                            onClick={() => openBatchAction('restore', b)}
                            disabled={!batchActionsEnabled}
                            className="inline-flex h-7 items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-50"
                          >
                            <FaIcon className="fas fa-rotate-left" aria-hidden="true" />
                            Восстановить
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => openBatchAction('delete', b)}
                            disabled={!batchActionsEnabled}
                            className="inline-flex h-7 items-center gap-1 rounded-full border border-rose-200 bg-white px-2.5 text-xs font-semibold text-rose-600 hover:bg-rose-50 disabled:cursor-wait disabled:opacity-50"
                          >
                            <FaIcon className="fas fa-trash-can" aria-hidden="true" />
                            Удалить
                          </button>
                        ))}
                      </td>
                    </tr>
                    {b.is_deleted && (
                      <tr className="bg-rose-50/50">
                        <td colSpan={9} className="px-3 pb-2 text-xs text-rose-700">
                          Удалил: <b>{b.deleted_by_name || '—'}</b>, {fmtDateTime(b.deleted_at)}
                          {b.delete_reason ? ` · причина: ${b.delete_reason}` : ' · причина не указана'}
                        </td>
                      </tr>
                    )}
                    {historyState.batchId === b.id && (
                      <tr id={`tez-batch-history-${b.id}`} className="bg-slate-50">
                        <td colSpan={9} className="px-3 py-2">
                          {historyState.status === 'loading' ? (
                            <span
                              role="status"
                              className="inline-flex items-center gap-1.5 text-xs text-slate-500"
                            >
                              <FaIcon className="fas fa-spinner fa-spin" aria-hidden="true" />
                              Загружаем историю…
                            </span>
                          ) : historyState.status === 'error' ? (
                            <div
                              role="alert"
                              className="flex flex-wrap items-center justify-between gap-2 text-xs text-rose-700"
                            >
                              <span>{historyState.error}</span>
                              <button
                                type="button"
                                onClick={() => toggleHistory(b.id, true)}
                                className="rounded-full border border-rose-200 bg-white px-2.5 py-1 font-semibold hover:bg-rose-50"
                              >
                                Повторить
                              </button>
                            </div>
                          ) : historyState.events.length ? (
                            <ul className="space-y-1 text-xs text-slate-600">
                              {historyState.events.map((e) => (
                                <li key={e.id} className="flex flex-wrap gap-x-2">
                                  <span className="tabular-nums text-slate-400">{fmtDateTime(e.created_at)}</span>
                                  <span className={`font-semibold ${e.action === 'delete' ? 'text-rose-700' : 'text-emerald-700'}`}>
                                    {e.action === 'delete' ? 'удаление' : 'восстановление'}
                                  </span>
                                  <span>{e.actor_name || '—'}</span>
                                  <span className="text-slate-400">
                                    {e.action === 'delete'
                                      ? `лидов убрано ${e.leads_removed}, оставлено ${e.leads_kept}, успешек ${e.successes_removed}`
                                      : `лидов возвращено ${e.leads_restored}, успешек ${e.successes_restored}` +
                                        (e.leads_merged ? `, слито ${e.leads_merged}` : '')}
                                  </span>
                                  {e.reason && <span className="italic">«{e.reason}»</span>}
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <span className="text-xs text-slate-400">Загрузку не удаляли — история пуста.</span>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
                {!visibleBatches.length && (
                  <tr>
                    <td colSpan={9} className="px-3 py-6 text-center text-gray-400">
                      {statsLoading
                        ? 'Загружаем список…'
                        : deletedCount
                          ? 'Все загрузки этого месяца удалены — включите «Показывать удалённые»'
                          : 'Базу за этот месяц ещё не загружали'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'leads' && (
          <div>
            <div className="flex flex-wrap gap-2 p-2 border-b bg-gray-50">
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="text-sm border rounded-lg px-2 py-1.5"
              >
                <option value="">Все статусы</option>
                {Object.entries(STATUS_LABELS).map(([key, label]) => (
                  <option key={key} value={key}>{label}</option>
                ))}
              </select>
              <select
                value={operatorFilter}
                onChange={(e) => setOperatorFilter(e.target.value)}
                className="text-sm border rounded-lg px-2 py-1.5 max-w-[220px]"
              >
                <option value="">Все операторы</option>
                {(currentStats?.operators || []).filter((o) => o.operator_id).map((o) => (
                  <option key={o.operator_id} value={o.operator_id}>
                    {o.operator_name} ({o.successes})
                  </option>
                ))}
              </select>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="ФИО или номер"
                className="text-sm border rounded-lg px-2 py-1.5 flex-1 min-w-[180px]"
              />
              {(statusFilter || operatorFilter || search) && (
                <button
                  type="button"
                  onClick={() => { setStatusFilter(''); setOperatorFilter(''); setSearch(''); }}
                  className="inline-flex h-8 items-center gap-1 rounded-full border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 hover:bg-slate-50"
                >
                  <FaIcon className="fas fa-xmark" />
                  Сбросить
                </button>
              )}
            </div>
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="text-left px-3 py-2">ФИО</th>
                  <th className="text-left px-3 py-2">Телефон</th>
                  <th className="text-left px-3 py-2">Статус</th>
                  <th className="text-left px-3 py-2">Оператор</th>
                  <th className="text-left px-3 py-2">Звонок</th>
                  <th className="text-left px-3 py-2">Поездки</th>
                </tr>
              </thead>
              <tbody>
                {leads.map((row) => (
                  <tr key={row.lead_id} className="border-t" title={RULE_LABELS[row.status_rule] || ''}>
                    <td className="px-3 py-2">
                      {row.full_name || '—'}
                      {row.upload_count > 1 && (
                        <span className="ml-2 text-[11px] text-gray-400">×{row.upload_count}</span>
                      )}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{row.phone}</td>
                    <td className="px-3 py-2">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLES[row.status] || ''}`}>
                        {STATUS_LABELS[row.status] || row.status}
                      </span>
                      {RULE_LABELS[row.status_rule] && (
                        <div className="mt-1 max-w-[240px] text-[11px] leading-tight text-gray-500">
                          {RULE_LABELS[row.status_rule]}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">{row.operator_name || '—'}</td>
                    <td className="px-3 py-2 text-gray-500">{fmtDateTime(row.call_at)}</td>
                    <td className="px-3 py-2"><LeadTripDates row={row} /></td>
                  </tr>
                ))}
                {!leads.length && (
                  <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400">Ничего не найдено</td></tr>
                )}
              </tbody>
            </table>
            {leadsTotal > 0 && (
              <div className="flex flex-wrap items-center justify-between gap-2 border-t bg-gray-50 px-3 py-2 text-xs text-slate-600">
                <span>
                  {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, leadsTotal)} из {leadsTotal}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => loadLeads(page - 1)}
                    disabled={page <= 1}
                    className="inline-flex h-8 items-center gap-1 rounded-full border border-slate-200 bg-white px-3 font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <FaIcon className="fas fa-chevron-left" />
                    Назад
                  </button>
                  <span className="px-2 tabular-nums">{page} / {leadsPages}</span>
                  <button
                    type="button"
                    onClick={() => loadLeads(page + 1)}
                    disabled={page >= leadsPages}
                    className="inline-flex h-8 items-center gap-1 rounded-full border border-slate-200 bg-white px-3 font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Вперёд
                    <FaIcon className="fas fa-chevron-right" />
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default TezLeadsPanel;
