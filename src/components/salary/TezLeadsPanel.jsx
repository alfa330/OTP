import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import InfoHint from '../common/InfoHint';

/**
 * База лидов TEZ ОП и статистика успешек.
 *
 * База помесячная и накопительная внутри месяца: один номер, загруженный дважды
 * за июнь — это один лид с upload_count=2, а тот же номер в июльской базе — уже
 * другой лид. Успешка датируется днём первой поездки водителя, поэтому лид из
 * июньской базы может дать успешку в июле (звонок в июне, поездка до 7-го).
 *
 * Props:
 *  - apiBaseUrl: базовый URL API
 *  - userId: id текущего пользователя (заголовок X-User-Id)
 *  - departmentId: id отдела
 *  - groupId: id выбранной группы (сужает рейтинг операторов и разбивку по дням;
 *             воронка и загрузки остаются на уровне отдела — база лидов общая)
 *  - month: 'YYYY-MM'
 *  - canEdit: можно ли загружать базу и запускать сверку
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
  prev_month_week1: 'Звонок в прошлом месяце, поездка до 7-го',
  no_call_before_trip: 'Нет звонка до поездки',
  trip_after_day7: 'Поездка позже 7-го числа',
  active_prev_month: 'Были заказы в прошлом месяце — уже работал',
};

const fmtDateTime = (value) => (value ? String(value).replace('T', ' ').slice(0, 16) : '—');

const TezLeadsPanel = ({ apiBaseUrl = '', userId, departmentId, groupId = null, month, canEdit = false }) => {
  const [stats, setStats] = useState(null);
  const [leads, setLeads] = useState([]);
  const [tab, setTab] = useState('operators');
  const [statusFilter, setStatusFilter] = useState('');
  const [operatorFilter, setOperatorFilter] = useState('');
  const [search, setSearch] = useState('');
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [msg, setMsg] = useState('');
  const [invalidRows, setInvalidRows] = useState([]);
  const [page, setPage] = useState(1);
  const [leadsTotal, setLeadsTotal] = useState(0);
  const [leadsPages, setLeadsPages] = useState(1);
  const fileRef = useRef(null);
  const pollRef = useRef(null);

  const PAGE_SIZE = 50;

  const [year, monthNum] = String(month || '').split('-').map((v) => parseInt(v, 10));
  const validPeriod = Number.isFinite(year) && Number.isFinite(monthNum);
  const headers = useMemo(() => ({ 'X-User-Id': userId }), [userId]);

  const loadStats = useCallback(() => {
    if (!validPeriod || !userId) return Promise.resolve(null);
    return axios
      .get(`${apiBaseUrl}/api/tez_leads/stats`, {
        params: { year, month: monthNum, group_id: groupId || undefined },
        headers,
      })
      .then((resp) => {
        setStats(resp?.data || null);
        return resp?.data || null;
      })
      .catch(() => null);
  }, [apiBaseUrl, headers, userId, year, monthNum, validPeriod, groupId]);

  const loadLeads = useCallback((toPage = 1) => {
    if (!validPeriod || !userId) return;
    axios
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
        const d = resp?.data || {};
        setLeads(d.leads || []);
        setLeadsTotal(d.total || 0);
        setLeadsPages(d.pages || 1);
        setPage(d.page || toPage);
      })
      .catch(() => { setLeads([]); setLeadsTotal(0); setLeadsPages(1); });
  }, [apiBaseUrl, headers, userId, year, monthNum, validPeriod, statusFilter, operatorFilter, search]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  // Смена вкладки/фильтров/поиска — всегда с первой страницы.
  useEffect(() => {
    if (tab === 'leads') loadLeads(1);
  }, [tab, loadLeads]);

  // Проверка базы на «уже работающих» идёт в фоне — дожидаемся её, опрашивая статус.
  const pollBatch = useCallback((batchId, attempt = 0) => {
    if (attempt > 60) return;
    pollRef.current = setTimeout(() => {
      loadStats().then((data) => {
        const batch = (data?.batches || []).find((b) => b.id === batchId);
        if (!batch || batch.check_status === 'pending' || batch.check_status === 'running') {
          pollBatch(batchId, attempt + 1);
        } else if (batch.check_status === 'error') {
          setMsg(`Проверка базы не удалась: ${batch.check_error || 'неизвестная ошибка'}`);
        } else {
          setMsg(`Проверка завершена: уже работающих — ${batch.already_working}`);
          setTimeout(() => setMsg(''), 6000);
        }
      });
    }, 3000);
  }, [loadStats]);

  useEffect(() => () => clearTimeout(pollRef.current), []);

  const upload = useCallback(() => {
    const file = fileRef.current?.files?.[0];
    if (!file || !validPeriod) return;
    const form = new FormData();
    form.append('file', file);
    form.append('year', year);
    form.append('month', monthNum);
    if (departmentId) form.append('department_id', departmentId);

    setUploading(true);
    setMsg('');
    setInvalidRows([]);
    axios
      .post(`${apiBaseUrl}/api/tez_leads/upload`, form, { headers })
      .then((resp) => {
        const d = resp?.data || {};
        setMsg(
          `Загружено ${d.rows_total}: новых ${d.rows_new}, дублей ${d.rows_duplicate}, ` +
          `невалидных ${d.rows_invalid}. Идёт проверка на уже работающих…`
        );
        setInvalidRows(d.invalid_rows || []);
        if (fileRef.current) fileRef.current.value = '';
        loadStats();
        if (d.batch_id) pollBatch(d.batch_id);
      })
      .catch((err) => setMsg(err?.response?.data?.error || 'Не удалось загрузить базу'))
      .finally(() => setUploading(false));
  }, [apiBaseUrl, headers, departmentId, year, monthNum, validPeriod, loadStats, pollBatch]);

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

  const funnel = stats?.funnel || {};

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
      <div className="flex flex-wrap items-center justify-between gap-2">
        {canEdit ? (
          <div className="flex flex-col sm:flex-row sm:items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.xlsx,.xlsm"
              className="text-sm file:mr-3 file:px-3 file:py-2 file:rounded-full file:border-0 file:bg-indigo-100 file:text-indigo-700"
            />
            <button
              onClick={upload}
              disabled={uploading}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-semibold text-white shadow-sm ${
                uploading ? 'bg-indigo-300 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700'
              }`}
            >
              <FaIcon className="fas fa-upload" />
              {uploading ? 'Загрузка…' : 'Загрузить базу'}
            </button>
            <span className="inline-flex items-center gap-1 text-xs text-slate-500">
              Колонки: fio, phone
              <InfoHint title="Формат файла" side="left">
                CSV или Excel с колонками <b>fio</b> и <b>phone</b>. Шапка необязательна — тогда
                первая колонка считается именем, вторая телефоном. Телефон в любом формате
                (8700…, +7 700…, 700…) приводится к 11 цифрам. База помесячная: тот же номер,
                загруженный повторно за месяц, не создаёт дубль, а увеличивает счётчик загрузок.
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

      {msg && <div className="text-sm font-medium text-indigo-800 bg-indigo-50 border border-indigo-100 rounded-xl px-3 py-2">{msg}</div>}

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
                  <th className="text-left px-2 py-1 font-medium">Строка</th>
                  <th className="text-left px-2 py-1 font-medium">ФИО</th>
                  <th className="text-left px-2 py-1 font-medium">Номер в файле</th>
                </tr>
              </thead>
              <tbody>
                {invalidRows.map((r, i) => (
                  <tr key={i} className="border-t border-rose-50">
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
              {(stats?.operators || []).map((row) => (
                <tr key={row.operator_id || row.operator_name} className="border-t">
                  <td className="px-3 py-2">{row.operator_name || '—'}</td>
                  <td className="px-3 py-2 text-right font-semibold">{row.successes}</td>
                  <td className="px-3 py-2 text-gray-500">{row.first_success || '—'}</td>
                  <td className="px-3 py-2 text-gray-500">{row.last_success || '—'}</td>
                </tr>
              ))}
              {!(stats?.operators || []).length && (
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
              {(stats?.by_day || []).map((row) => (
                <tr key={row.date} className="border-t">
                  <td className="px-3 py-2">{row.date}</td>
                  <td className="px-3 py-2 text-right font-semibold">{row.successes}</td>
                </tr>
              ))}
              {!(stats?.by_day || []).length && (
                <tr><td colSpan={2} className="px-3 py-6 text-center text-gray-400">Нет данных</td></tr>
              )}
            </tbody>
          </table>
        )}

        {tab === 'batches' && (
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
              </tr>
            </thead>
            <tbody>
              {(stats?.batches || []).map((b) => (
                <tr key={b.id} className="border-t">
                  <td className="px-3 py-2">{b.file_name}</td>
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
                </tr>
              ))}
              {!(stats?.batches || []).length && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-gray-400">Базу за этот месяц ещё не загружали</td></tr>
              )}
            </tbody>
          </table>
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
                {(stats?.operators || []).filter((o) => o.operator_id).map((o) => (
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
                  <th className="text-left px-3 py-2">Первая поездка</th>
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
                    </td>
                    <td className="px-3 py-2">{row.operator_name || '—'}</td>
                    <td className="px-3 py-2 text-gray-500">{fmtDateTime(row.call_at)}</td>
                    <td className="px-3 py-2 text-gray-500">{fmtDateTime(row.first_order_at)}</td>
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
