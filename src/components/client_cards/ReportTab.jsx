/* Отчёт «Карточки клиента» (задача #335, п. 11): причины обращений деревом (основная
 * причина → подтемы всех уровней), города, дни и операторы за период — из заполненных
 * карточек, без ручного сбора.
 *
 * Каждая доля подписана: от чего она считается, видно в заголовке колонки и под «i».
 * Карточка с двумя причинами входит в строки обеих, поэтому доли причин в сумме бывают
 * больше 100 % — об этом сказано там же, а не оставлено на догадку.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { BarChart3, ChevronDown, Download, Loader2 } from 'lucide-react';

import IosDateRangePicker from '../ui/DateRangePicker';
import { IosHint, iosBtnSecondary, iosCard, iosGroupLabel } from '../ui/ios';
import {
    NO_ACCOUNT_LABEL as NO_ACCOUNT, almatyToday, errorText, fmtSeconds, fmtShare, periodPreset, reportReasonRows,
} from './cardRules';
import { periodPresets } from './JournalTab';
import { EmptyState, Spinner } from './ui';

const CITY_UNKNOWN = 'Город не указан';

const Tile = ({ label, value, sub = '', hint = '', warn = false }) => (
    <div className={`${iosCard} px-4 py-3`}>
        <div className="flex items-center gap-1.5 text-[12px] text-slate-500">
            <span>{label}</span>
            {hint && <IosHint text={hint} label={`Как считается «${label}»`} />}
        </div>
        <div className={`mt-1 text-[22px] font-semibold tabular-nums leading-tight ${warn ? 'text-rose-600' : 'text-slate-900'}`}>{value}</div>
        {sub && <div className="text-[12px] tabular-nums text-slate-500">{sub}</div>}
    </div>
);

/* Строка разреза: название, число, доля и тонкая полоса доли. Полоса одного цвета —
   это длина, а не оценка. */
const BarRow = ({ title, count, whole, bold = false, depth = 0, muted = false, tree = false, onToggle = null, open = false }) => {
    const share = whole ? Math.min(100, (count / whole) * 100) : 0;
    const body = (
        <>
            {/* Уровень дерева причин — отступом: подтема стоит под своей причиной. */}
            <span className="flex min-w-0 items-center gap-1.5" style={depth ? { paddingLeft: depth * 20 } : undefined}>
                {onToggle && <ChevronDown size={14} className={`shrink-0 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />}
                {/* В дереве причина без подтем держит место стрелки: названия уровня стоят в одну линию. */}
                {tree && !onToggle && <span className="w-3.5 shrink-0" aria-hidden="true" />}
                <span className={`min-w-0 truncate ${bold ? 'font-medium text-slate-900' : muted ? 'text-slate-500' : 'text-slate-700'}`}>{title}</span>
            </span>
            <span className="hidden h-1.5 overflow-hidden rounded-full bg-slate-100 sm:block">
                <span className="block h-full rounded-full bg-slate-400" style={{ width: `${share}%` }} />
            </span>
            <span className={`text-right tabular-nums ${bold ? 'font-semibold text-slate-900' : 'text-slate-700'}`}>{count}</span>
            <span className="text-right tabular-nums text-slate-500">{fmtShare(count, whole)}</span>
        </>
    );
    const grid = 'grid w-full grid-cols-[minmax(0,1fr)_56px_72px] items-center gap-3 px-4 py-2 text-left text-[13px] sm:grid-cols-[minmax(0,1.4fr)_minmax(60px,1fr)_56px_72px]';
    return onToggle
        ? <button type="button" onClick={onToggle} aria-expanded={open} className={`${grid} hover:bg-slate-50`}>{body}</button>
        : <div className={grid}>{body}</div>;
};

const TableHead = ({ first, shareHint }) => (
    <div className="grid grid-cols-[minmax(0,1fr)_56px_72px] items-center gap-3 border-b border-slate-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 sm:grid-cols-[minmax(0,1.4fr)_minmax(60px,1fr)_56px_72px]">
        <span>{first}</span>
        <span className="hidden sm:block" />
        <span className="text-right">Карточек</span>
        <span className="flex items-center justify-end gap-1">Доля <IosHint text={shareHint} label="От чего доля" align="right" /></span>
    </div>
);

export default function ReportTab({ api, departmentId, toast }) {
    const [range, setRange] = useState(() => periodPreset('month', almatyToday()));
    const [data, setData] = useState(null);
    const [failed, setFailed] = useState('');
    const [open, setOpen] = useState(() => new Set());
    const [exporting, setExporting] = useState(false);

    const params = useMemo(() => ({ department_id: departmentId, from: range.from, to: range.to }), [departmentId, range]);

    const load = useCallback(async () => {
        try {
            const { data: body } = await api.report(params);
            setData(body);
            setFailed('');
        } catch (error) {
            setFailed(errorText(error, 'Отчёт не загрузился'));
        }
    }, [api, params]);

    useEffect(() => { setData(null); load(); }, [load]);

    const exportXlsx = async () => {
        if (exporting) return;
        setExporting(true);
        try {
            const response = await api.reportExport(params);
            const disposition = String(response.headers?.['content-disposition'] || '');
            const match = /filename\*=UTF-8''([^;]+)/i.exec(disposition) || /filename="?([^";]+)"?/i.exec(disposition);
            const name = match ? decodeURIComponent(match[1]) : `Карточка клиента ${range.from}–${range.to}.xlsx`;
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = name;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 60000);
        } catch (error) {
            toast(errorText(error, 'Выгрузка не собралась'), 'error');
        } finally {
            setExporting(false);
        }
    };

    const toggle = (id) => setOpen((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    const totals = data?.totals;
    const done = totals?.done || 0;
    const noAccount = totals?.no_account || 0;
    const shareHint = `Доля = карточек ÷ заполненных карточек за период (${done}). Не от всех звонков: у незаполненной карточки причины и города нет.`;

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2.5">
                <IosDateRangePicker from={range.from} to={range.to} max={almatyToday()} presets={periodPresets()}
                                    onChange={({ from, to }) => { if (from && to) setRange({ from, to }); }} />
                <button type="button" onClick={exportXlsx} disabled={exporting || !data || !totals?.calls} className={iosBtnSecondary}>
                    {exporting ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Выгрузить в Excel
                </button>
            </div>

            {failed && (
                <EmptyState title={failed}>
                    <button type="button" onClick={load} className={`${iosBtnSecondary} mt-4`}>Повторить</button>
                </EmptyState>
            )}
            {!failed && !data && <Spinner text="Считаем отчёт…" />}
            {data && !totals.calls && (
                <EmptyState icon={BarChart3} title="За период звонков с карточкой не было"
                            text="Отчёт собирается сам из карточек, которые операторы заполняют в iCORE Phone." />
            )}

            {data && totals.calls > 0 && (
                <>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                        <Tile label="Звонков" value={totals.calls} sub={`входящих ${totals.incoming} · исходящих ${totals.outgoing}`}
                              hint="Состоявшиеся звонки, по которым телефон открыл карточку. Звонки без разговора и без записи оператора не считаются." />
                        <Tile label="Заполнено" value={done} sub={`${fmtShare(done, totals.calls)} от звонков`}
                              hint="Оператор выбрал аккаунт (или отметил «Аккаунта нет») и причину и сохранил карточку. Доля = заполнено ÷ звонков." />
                        <Tile label="Не заполнено" value={totals.pending} warn={totals.pending > 0}
                              sub={totals.pending ? `${fmtShare(totals.pending, totals.calls)} от звонков` : ''}
                              hint="Звонок состоялся, а карточка не сохранена. В разрезах по причинам и городам таких карточек нет." />
                        <Tile label="Разных людей" value={totals.people}
                              hint="По аккаунту, а где его нет — по номеру. Один человек с пятью звонками — один." />
                        <Tile label="Время на карточку" value={fmtSeconds(totals.avg_wrapup_sec)}
                              hint="Среднее от конца звонка до сохранения карточки. Карточки, сохранённые ещё во время разговора, в среднее не входят." />
                        <Tile label="Позже срока" value={totals.late}
                              sub={totals.late ? `${fmtShare(totals.late, done)} от заполненных` : ''}
                              hint="Карточка сохранена после того, как вышло время постобработки. Доля = позже срока ÷ заполнено." />
                    </div>

                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Причины обращений</div>
                            <div className={`${iosCard} overflow-hidden`}>
                                <TableHead first="Причина и подтемы"
                                           shareHint={`${shareHint} Карточка с двумя причинами входит в строки обеих, поэтому доли в сумме бывают больше 100 %. Число у причины с подтемами — карточки с любой её подтемой, без двойного счёта.`} />
                                {!data.categories.length && <p className="px-4 py-4 text-[13px] text-slate-500">Заполненных карточек за период нет.</p>}
                                {/* Дерево причин: ветка раскрывается нажатием, уровень за уровнем. */}
                                {data.categories.map((category) => (
                                    <div key={category.id} className="border-b border-slate-100 last:border-b-0">
                                        {reportReasonRows([category], (id) => open.has(id)).map((row) => (
                                            <BarRow key={row.key} tree title={row.title} count={row.count} whole={done} depth={row.depth}
                                                    bold={row.depth === 0} muted={row.muted} open={row.open}
                                                    onToggle={row.hasChildren ? () => toggle(row.id) : null} />
                                        ))}
                                    </div>
                                ))}
                            </div>
                        </section>

                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Города</div>
                            <div className={`${iosCard} overflow-hidden`}>
                                <TableHead first="Город аккаунта" shareHint={`${shareHint} У карточки один город, сумма строк равна числу заполненных.`} />
                                {!data.cities.length && !noAccount && <p className="px-4 py-4 text-[13px] text-slate-500">Заполненных карточек за период нет.</p>}
                                {data.cities.map((city) => (
                                    <div key={city.city || 'none'} className="border-b border-slate-100 last:border-b-0">
                                        <BarRow title={city.city || CITY_UNKNOWN} count={city.count} whole={done} />
                                    </div>
                                ))}
                                {/* Обращения без аккаунта — своей строкой: города у них быть не может. */}
                                {noAccount > 0 && (
                                    <div className="border-b border-slate-100 last:border-b-0">
                                        <BarRow title={NO_ACCOUNT} count={noAccount} whole={done} />
                                    </div>
                                )}
                            </div>
                        </section>
                    </div>

                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Операторы</div>
                            <div className={`${iosCard} overflow-x-auto`}>
                                <table className="w-full min-w-[520px] text-[13px]">
                                    <thead>
                                        <tr className="border-b border-slate-100 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                            <th className="px-4 py-2 text-left font-semibold">Оператор</th>
                                            <th className="px-2 py-2 text-right font-semibold">Звонков</th>
                                            <th className="px-2 py-2 text-right font-semibold">Заполнено</th>
                                            <th className="px-2 py-2 text-right font-semibold">Не заполнено</th>
                                            <th className="px-2 py-2 text-right font-semibold">Позже срока</th>
                                            <th className="px-4 py-2 text-right font-semibold">На карточку</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                        {data.operators.map((operator) => (
                                            <tr key={operator.id}>
                                                <td className="px-4 py-2 text-slate-900">{operator.name}</td>
                                                <td className="px-2 py-2 text-right tabular-nums text-slate-700">{operator.calls}</td>
                                                <td className="px-2 py-2 text-right tabular-nums text-slate-700">
                                                    {operator.done} <span className="text-slate-400">· {fmtShare(operator.done, operator.calls)}</span>
                                                </td>
                                                <td className={`px-2 py-2 text-right tabular-nums ${operator.pending ? 'font-medium text-rose-600' : 'text-slate-400'}`}>{operator.pending}</td>
                                                <td className="px-2 py-2 text-right tabular-nums text-slate-700">{operator.late}</td>
                                                <td className="px-4 py-2 text-right tabular-nums text-slate-700">{fmtSeconds(operator.avg_wrapup_sec)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </section>

                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>По дням</div>
                            <div className={`${iosCard} max-h-[340px] overflow-y-auto`}>
                                <table className="w-full text-[13px]">
                                    <thead className="sticky top-0 bg-white">
                                        <tr className="border-b border-slate-100 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                            <th className="px-4 py-2 text-left font-semibold">Дата</th>
                                            <th className="px-2 py-2 text-right font-semibold">Звонков</th>
                                            <th className="px-4 py-2 text-right font-semibold">Заполнено</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                        {data.days.map((day) => (
                                            <tr key={day.date}>
                                                <td className="px-4 py-2 tabular-nums text-slate-900">{day.date.split('-').reverse().join('.')}</td>
                                                <td className="px-2 py-2 text-right tabular-nums text-slate-700">{day.calls}</td>
                                                <td className="px-4 py-2 text-right tabular-nums text-slate-700">
                                                    {day.done} <span className="text-slate-400">· {fmtShare(day.done, day.calls)}</span>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </section>
                    </div>

                    <p className="px-1 text-[12px] text-slate-500">
                        Запросов в другие отделы за период: {data.requests.total}
                        {data.requests.total ? `, без решения — ${data.requests.active}` : ''}
                        {data.requests.avg_first_reply_sec !== null ? `; первый ответ отдела в среднем через ${fmtSeconds(data.requests.avg_first_reply_sec)}` : ''}.
                    </p>
                </>
            )}
        </div>
    );
}
