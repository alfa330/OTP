import React, { useEffect, useMemo, useState } from 'react';
import { BarChart3, ChevronRight, Download, History } from 'lucide-react';
import { IosPager } from '../ui/ios';
import { IosDateRangePicker } from '../ui/DateRangePicker';
import { apiError, saveBlob } from './disciplineApi';
import { dBtnSecondary, EmptyState, Group, LoadingRows, T } from './disciplineUi';
import { fmtDate, fmtDateTime, fmtMoney, todayIso } from './disciplineRules';

/*
 * «Отчёты» (ТЗ п. 15) и «Журнал действий» (п. 14) — руководителю. Каждый отчёт
 * — с фильтром по периоду и выгрузкой в Excel. «По мерам» — это и есть файл на
 * удержания: все утверждённые меры с финансовым эффектом, включая процент.
 * Журнал действий только дописывается: правок и удалений в нём не бывает.
 */

const REPORTS = [
    { key: 'operators', title: 'Сводка по операторам', text: 'Нарушения, типы, объяснительные, акты и меры по каждому' },
    { key: 'types', title: 'По типам нарушений', text: 'Какие нарушения преобладают, по месяцам' },
    { key: 'supervisors', title: 'По супервайзерам', text: 'Кто сколько зафиксировал — перекос видно сразу' },
    { key: 'groups', title: 'По группам и линиям', text: 'Где с дисциплиной хуже' },
    { key: 'measures', title: 'По мерам (удержания)', text: 'Утверждённые меры с суммами — файл для расчёта' },
    { key: 'controls', title: 'Особый контроль', text: 'Поставлено, прошли без нарушений, дошли до расторжения' },
    { key: 'audit', title: 'Журнал действий', text: 'Кто что сделал и что открывал', icon: History },
];

const monthStart = () => `${todayIso().slice(0, 8)}01`;

// Длинные тексты переносятся, остальное (ФИО, группа, числа, даты) — в строку.
const WRAP_KEYS = new Set(['types', 'measures', 'reason', 'changes_text', 'decision_detail', 'measure_title', 'type_title',
    'basis']);

const formatCell = (key, value) => {
    if (value == null || value === '') return '—';
    if (typeof value === 'number') return key.includes('amount') ? fmtMoney(value) : value;
    const text = String(value);
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(text)) return fmtDateTime(text);
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return fmtDate(text);
    return text;
};

const ReportsPanel = ({ api, toast, departmentId, version }) => {
    const [period, setPeriod] = useState({ from: monthStart(), to: todayIso() });
    const [selected, setSelected] = useState('operators');
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [exporting, setExporting] = useState(false);

    const params = useMemo(() => ({
        date_from: period.from, date_to: period.to, ...(departmentId ? { department_id: departmentId } : {}),
    }), [period, departmentId]);

    // Страница журнала действий принадлежит своему отчёту и периоду: сменили их —
    // снова первая, и сразу, без второго запроса со старой страницей.
    const pageKey = `${selected}|${period.from}|${period.to}|${departmentId || ''}`;
    const [pageState, setPageState] = useState({ key: pageKey, page: 1 });
    const page = pageState.key === pageKey ? pageState.page : 1;
    const setPage = (value) => setPageState({ key: pageKey, page: value });

    useEffect(() => {
        let alive = true;
        setData(null);
        setError(null);
        const request = selected === 'audit' ? api.audit({ ...params, page }) : api.report(selected, params);
        request
            .then((payload) => { if (alive) { setData(payload); setError(null); } })
            .catch((err) => { if (alive) setError(apiError(err, 'Отчёт не собрался')); });
        return () => { alive = false; };
    }, [api, selected, params, page, version]);

    const exportExcel = async () => {
        setExporting(true);
        try {
            const blob = selected === 'audit' ? await api.auditXlsx(params) : await api.reportXlsx(selected, params);
            const title = REPORTS.find((r) => r.key === selected)?.title || selected;
            saveBlob(blob, `Дисциплина_${title}_${todayIso()}.xlsx`);
        } catch (err) {
            toast(apiError(err, 'Выгрузка не получилась'), 'error');
        } finally {
            setExporting(false);
        }
    };

    const auditColumns = [
        { key: 'created_at', title: 'Когда' }, { key: 'actor_name', title: 'Кто' }, { key: 'action_label', title: 'Действие' },
        { key: 'operator_name', title: 'Сотрудник' }, { key: 'changes_text', title: 'Подробности' },
    ];
    const columns = selected === 'audit' ? auditColumns : (data?.columns || []);
    const rows = selected === 'audit' ? (data?.items || []) : (data?.rows || []);

    return (
        <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)] xl:grid-cols-[340px_minmax(0,1fr)] 2xl:gap-6">
            <div className="space-y-3">
                <IosDateRangePicker roomy from={period.from} to={period.to} max={todayIso()}
                                    onChange={({ from, to }) => setPeriod({ from: from || monthStart(), to: to || todayIso() })} />
                <ul className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                    {REPORTS.map((report) => {
                        const Icon = report.icon || BarChart3;
                        const active = report.key === selected;
                        return (
                            <li key={report.key} className="border-b border-slate-100 last:border-0">
                                <button type="button" onClick={() => setSelected(report.key)} aria-current={active}
                                        className={`flex w-full items-center gap-3 px-4 py-3 text-left transition ${active ? 'bg-blue-50' : 'hover:bg-slate-50'}`}>
                                    <Icon size={16} className={active ? 'text-blue-600' : 'text-slate-400'} />
                                    <span className="min-w-0 flex-1">
                                        <span className={`block text-[13.5px] font-medium ${active ? 'text-blue-800' : 'text-slate-900'} ${T.body}`}>{report.title}</span>
                                        <span className={`block text-[11.5px] text-slate-500 ${T.note}`}>{report.text}</span>
                                    </span>
                                    <ChevronRight size={15} className="text-slate-300 lg:hidden" />
                                </button>
                            </li>
                        );
                    })}
                </ul>
            </div>

            <div className="min-w-0 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className={`text-[16px] font-semibold text-slate-900 ${T.body}`}>
                        {REPORTS.find((r) => r.key === selected)?.title}
                    </h2>
                    <button type="button" className={dBtnSecondary} onClick={exportExcel} disabled={exporting}>
                        <Download size={15} />
                        {exporting ? 'Готовим файл…' : 'Excel'}
                    </button>
                </div>

                {selected === 'controls' && data?.summary && (
                    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                        {[['Поставлено', data.summary.started], ['Без нарушений', data.summary.clean],
                            ['До расторжения', data.summary.termination], ['Ещё на контроле', data.summary.active]].map(([label, value]) => (
                            <div key={label} className="rounded-2xl bg-white px-4 py-3 ring-1 ring-slate-200/70">
                                <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</div>
                                <div className="mt-1 text-[22px] font-semibold tabular-nums text-slate-900">{value ?? 0}</div>
                            </div>
                        ))}
                    </div>
                )}

                {error ? <Group><EmptyState title={error} /></Group> : !data ? <LoadingRows rows={6} /> : !rows.length ? (
                    <Group><EmptyState title="За период данных нет" /></Group>
                ) : (
                    <div className="overflow-x-auto rounded-2xl bg-white ring-1 ring-slate-200/70">
                        <table className="w-full min-w-[560px] text-left text-[13px] 2xl:text-[15px]">
                            <thead className="bg-slate-50/80 text-[11.5px] uppercase tracking-wide text-slate-500 2xl:text-[12.5px]">
                                <tr>{columns.map((c) => <th key={c.key} className="px-4 py-2.5 font-semibold">{c.title}</th>)}</tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {rows.slice(0, 300).map((row, index) => (
                                    <tr key={row.id || index} className="align-top">
                                        {columns.map((c) => (
                                            <td key={c.key} className={`px-4 py-2.5 text-slate-700 ${typeof row[c.key] === 'number' ? 'tabular-nums' : ''} ${WRAP_KEYS.has(c.key) ? 'min-w-[220px]' : 'whitespace-nowrap'}`}>
                                                {formatCell(c.key, row[c.key])}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}

                {selected === 'measures' && (data?.totals || []).length > 0 && (
                    <Group title="Итог по мерам" cardClassName="p-0 overflow-hidden">
                        <ul className="divide-y divide-slate-100">
                            {data.totals.map((row) => (
                                <li key={row.measure_title} className="flex items-center justify-between gap-3 px-4 py-2.5 text-[13px]">
                                    <span className="text-slate-800">{row.measure_title}</span>
                                    <span className="tabular-nums text-slate-600">{row.count} · {fmtMoney(row.amount)}</span>
                                </li>
                            ))}
                        </ul>
                    </Group>
                )}

                {selected === 'audit' && data?.total > data?.page_size && (
                    <IosPager page={page} pageCount={Math.ceil(data.total / data.page_size)} total={data.total}
                              from={(page - 1) * data.page_size + 1} to={Math.min(page * data.page_size, data.total)}
                              onPage={setPage} />
                )}
                {selected !== 'audit' && rows.length > 300 && (
                    <div className={`px-1 text-[12px] text-slate-500 ${T.note}`}>Показаны первые 300 строк — полный отчёт в Excel.</div>
                )}
            </div>
        </div>
    );
};

export default ReportsPanel;
