import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { BarChart3, ChevronRight, Loader2, RefreshCw, Table2, TriangleAlert } from 'lucide-react';
import { Bar as RBar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { iosCard, iosGroupLabel, iosBtnGhost, IosHint, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { Bar, Metric, PagedTable, Td, Th } from '../wiki/reportKit';
import { buildPeriodOptions, monthLabel } from './dialListPeriods';
import { SIGN_BREAKDOWN, signMeta } from './signStatus';

/*
 * Вкладка «Аналитика» раздела «Обзвон» (просьба владельца 29.09.2026: сводку
 * сверху экрана убрать, чтобы не отвлекала, а показатели собрать отдельной
 * вкладкой).
 *
 * Всё — по базе одного месяца, как журнал: успешка принадлежит строке базы.
 * Порядок блоков — порядок вопросов руководителя:
 *   1) плитки — воронка базы: сколько обзвонили, с кем поговорили ≥10 с, сколько
 *      это дало успешек и какая конверсия. Каждое следующее число — часть
 *      предыдущего, поэтому отдельной «воронки» с теми же числами нет;
 *   2) документы — что Sapar говорит о подписании по всей базе (полоса + легенда
 *      с числами; нажатие открывает этих водителей в журнале);
 *   3) по дням — один показатель за раз (одна ось, один цвет), рядом таблица;
 *   4) операторы — кто сколько звонил, говорил и сколько получил успешек.
 *
 * Объяснения — под «i» (правило владельца 25.08.2026: на экране числа и
 * названия). Цвета документов — из signStatus.js, один на весь раздел.
 */

const plural = (n, one, few, many) => {
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b === 1) return one;
    if (b >= 2 && b <= 4) return few;
    return many;
};

const NF = new Intl.NumberFormat('ru-RU');
const num = (v) => NF.format(Number(v) || 0);
const pctText = (v) => (v == null ? '—' : `${String(v).replace('.', ',')}%`);
const pad2 = (n) => String(n).padStart(2, '0');

const fmtTalk = (sec) => {
    const s = Math.max(0, Number(sec) || 0);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h) return `${h} ч ${m} мин`;
    if (m) return `${m} мин`;
    return s ? `${s} с` : '—';
};

const fmtWhen = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const now = new Date();
    const clock = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    if (sameDay(d, now)) return `сегодня, ${clock}`;
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    if (sameDay(d, yesterday)) return `вчера, ${clock}`;
    const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
    if (sameDay(d, tomorrow)) return `завтра, ${clock}`;
    return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}, ${clock}`;
};

const dayLabel = (iso) => {
    const [, m, d] = String(iso).split('-');
    return `${Number(d)}.${m}`;
};

const METRICS = [
    { value: 'successes', label: 'Успешки', name: 'Успешек' },
    { value: 'talked', label: 'Разговоры ≥10 с', name: 'Разговоров ≥10 с' },
    { value: 'attempts', label: 'Звонки', name: 'Звонков' },
];
const CHART_COLOR = '#2a78d6';

const RULES_HINT = 'Раз в 3 часа сервер спрашивает Sapar по ИИН, подписал ли водитель документы за прошлый '
    + 'месяц: документы месяца приходят в следующем. Документов у водителя несколько, общий статус — худший '
    + 'из них: «подписал» — только когда подписаны все. Подписавший уходит из обзвона, а успешка '
    + 'засчитывается оператору, который последним поговорил с ним не меньше 10 секунд до подписи. '
    + 'Подписал без такого разговора — «подписал сам», успешки нет.';

const Notice = ({ tone = 'amber', children }) => (
    <div className={`flex items-start gap-2 rounded-xl px-3.5 py-2.5 text-[12.5px] ring-1 ${
        tone === 'rose' ? 'bg-rose-50 text-rose-700 ring-rose-100' : 'bg-amber-50 text-amber-800 ring-amber-100'}`}
    >
        <TriangleAlert size={14} className="mt-[1px] shrink-0" />
        <span>{children}</span>
    </div>
);

/* Строка проверки подписания: за какой месяц документы и когда спрашивали Sapar. */
const SignCheckLine = ({ check }) => {
    if (!check) return null;
    let when;
    if (!check.active) when = 'эта база больше не проверяется';
    else if (check.never_checked) when = 'первая проверка — в ближайшие полчаса';
    else if (check.last_checked_at) {
        when = `проверено ${fmtWhen(check.last_checked_at)}`;
        if (check.next_check_at) when += ` · следующая ${fmtWhen(check.next_check_at)}`;
    }
    return (
        <div className="flex min-w-0 items-center gap-1.5 text-[12.5px] text-slate-500">
            <span className="min-w-0 sm:truncate">
                Документы за <span className="font-medium text-slate-700">{(check.doc_month_label || '').toLowerCase()}</span>
                {when ? ` · ${when}` : ''}
            </span>
            <IosHint text={RULES_HINT} align="right" label="Как проверяется подписание" />
        </div>
    );
};

/* Документы: одна полоса «часть целого» + легенда-таблица с числами. Полоса без
   подписей внутри — сегменты бывают узкими; числа всегда в легенде. */
const DocumentsCard = ({ totals, check, onOpen }) => {
    const whole = Number(totals.leads) || 0;
    const rows = SIGN_BREAKDOWN.map((s) => ({ ...s, color: signMeta(s.key).color, value: Number(totals[s.total]) || 0 }))
        .filter((s) => s.value > 0 || s.key === 'signed');
    const shown = rows.filter((s) => s.value > 0);
    return (
        <section className="space-y-1.5">
            <div className="flex items-center gap-1.5">
                <span className={iosGroupLabel}>Документы{check?.doc_month_label ? ` за ${check.doc_month_label.toLowerCase()}` : ''}</span>
                <IosHint text="Состояние подписания по всей базе месяца. Нажмите на строку — откроется журнал с этими водителями." />
            </div>
            <div className={`${iosCard} p-4`}>
                {whole === 0 ? (
                    <div className="py-4 text-center text-[13px] text-slate-500">В базе этого месяца пока никого</div>
                ) : (
                    <>
                        <div
                            className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full"
                            role="img"
                            aria-label={shown.map((s) => `${s.label}: ${s.value}`).join(', ')}
                        >
                            {shown.map((s) => (
                                <div
                                    key={s.key}
                                    title={`${s.label}: ${num(s.value)}`}
                                    style={{ width: `${(100 * s.value) / whole}%`, backgroundColor: s.color, minWidth: 3 }}
                                />
                            ))}
                        </div>
                        <ul className="mt-3 divide-y divide-slate-100">
                            {rows.map((s) => {
                                const share = whole ? Math.round((100 * s.value) / whole) : 0;
                                const sub = s.key === 'signed' && s.value > 0
                                    ? [totals.self_signed ? `из них сами — ${num(totals.self_signed)}` : '',
                                        totals.signed_before_upload ? `ещё до загрузки базы — ${num(totals.signed_before_upload)}` : '',
                                        totals.pending_attribution ? `ждём конца звонка — ${num(totals.pending_attribution)}` : '']
                                        .filter(Boolean).join(' · ')
                                    : '';
                                return (
                                    <li key={s.key}>
                                        <button
                                            type="button"
                                            onClick={() => onOpen?.(s.stage ? { stage: s.stage } : { sign: s.sign })}
                                            disabled={!s.value}
                                            className="flex w-full items-center gap-3 py-2 text-left transition hover:bg-slate-50 disabled:cursor-default disabled:hover:bg-transparent"
                                        >
                                            <span className="h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ backgroundColor: s.color }} />
                                            <span className="min-w-0 flex-1">
                                                <span className="block truncate text-[13.5px] text-slate-800">{s.label}</span>
                                                {sub && <span className="block truncate text-[12px] text-slate-500">{sub}</span>}
                                            </span>
                                            <span className="shrink-0 text-[13.5px] font-semibold tabular-nums text-slate-900">{num(s.value)}</span>
                                            <span className="w-10 shrink-0 text-right text-[12px] tabular-nums text-slate-500">{share}%</span>
                                            <ChevronRight size={14} className={`shrink-0 ${s.value ? 'text-slate-300' : 'text-transparent'}`} />
                                        </button>
                                    </li>
                                );
                            })}
                        </ul>
                    </>
                )}
            </div>
        </section>
    );
};

const DayTooltip = ({ active, payload, metricName }) => {
    if (!active || !payload?.length) return null;
    const row = payload[0].payload;
    return (
        <div className="rounded-xl bg-white px-3 py-2 text-[12px] shadow-[0_4px_16px_rgba(15,23,42,0.10)] ring-1 ring-slate-200">
            <div className="text-[15px] font-semibold text-slate-900">{num(payload[0].value)}</div>
            <div className="text-slate-500">{metricName} · {dayLabel(row.day)}</div>
        </div>
    );
};

const ByDayCard = ({ days }) => {
    const [metric, setMetric] = useState('successes');
    const [view, setView] = useState('chart');
    const meta = METRICS.find((m) => m.value === metric) || METRICS[0];
    const total = days.reduce((sum, d) => sum + (Number(d[metric]) || 0), 0);
    return (
        <section className="space-y-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                    <span className={iosGroupLabel}>По дням</span>
                    <IosHint text="Звонки и разговоры — по дню звонка, успешки — по дню, когда водитель подписал документы. Всё — по водителям базы выбранного месяца." />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <IosSegmented value={metric} onChange={setMetric} options={METRICS} size="xs" ariaLabel="Показатель" />
                    <IosSegmented
                        value={view}
                        onChange={setView}
                        size="xs"
                        ariaLabel="Вид"
                        options={[
                            { value: 'chart', label: 'График', icon: <BarChart3 size={12} /> },
                            { value: 'table', label: 'Таблица', icon: <Table2 size={12} /> },
                        ]}
                    />
                </div>
            </div>
            <div className={`${iosCard} p-4`}>
                <div className="mb-2 text-[12.5px] text-slate-500">
                    {meta.name} за месяц: <span className="font-semibold text-slate-900">{num(total)}</span>
                </div>
                {view === 'chart' ? (
                    <div className="h-56" role="img" aria-label={`${meta.name} по дням`}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={days} margin={{ top: 6, right: 4, left: -22, bottom: 0 }}>
                                <CartesianGrid stroke="#eef1f5" vertical={false} />
                                <XAxis
                                    dataKey="day"
                                    tickFormatter={dayLabel}
                                    tick={{ fontSize: 10.5, fill: '#64748b' }}
                                    interval="preserveStartEnd"
                                    minTickGap={12}
                                    tickLine={false}
                                    axisLine={{ stroke: '#e2e8f0' }}
                                />
                                <YAxis tick={{ fontSize: 10.5, fill: '#64748b' }} tickLine={false} axisLine={false} allowDecimals={false} />
                                <Tooltip cursor={{ fill: 'rgba(42,120,214,0.06)' }} content={<DayTooltip metricName={meta.name} />} />
                                <RBar dataKey={metric} fill={CHART_COLOR} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                ) : (
                    <div className="max-h-72 overflow-y-auto">
                        <table className="w-full border-collapse">
                            <thead className="sticky top-0 border-b border-slate-100 bg-white">
                                <tr><Th>День</Th><Th right>Звонки</Th><Th right>Разговоры ≥10 с</Th><Th right>Время разговоров</Th><Th right>Подписали</Th><Th right>Успешки</Th></tr>
                            </thead>
                            <tbody className="divide-y divide-slate-50">
                                {[...days].reverse().map((d) => (
                                    <tr key={d.day}>
                                        <Td>{dayLabel(d.day)}</Td>
                                        <Td right>{num(d.attempts)}</Td>
                                        <Td right>{num(d.talked)}</Td>
                                        <Td right muted>{fmtTalk(d.talk_sec)}</Td>
                                        <Td right>{num(d.signed)}</Td>
                                        <Td right>{num(d.successes)}</Td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </section>
    );
};

const DialListAnalytics = ({
    apiBaseUrl, authHeaders, departmentId, periods = [], activePeriod = '', period = '', onPeriodChange, onOpenJournal,
}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // 'all' — режим журнала; аналитика всегда про один месяц.
    const shownPeriod = period && period !== 'all' ? period : '';

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const qs = shownPeriod ? `?period=${encodeURIComponent(shownPeriod)}` : '';
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/analytics${qs}`, {
                credentials: 'include', headers: authHeaders(),
            });
            const body = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(body?.error || `HTTP ${resp.status}`);
            setData(body);
        } catch (e) {
            setError(e.message || 'Не удалось загрузить аналитику');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, departmentId, shownPeriod]);

    useEffect(() => { load(); }, [load]);

    const periodOptions = useMemo(
        () => buildPeriodOptions(periods, { activePeriod, grouped: true }),
        [periods, activePeriod],
    );

    const t = data?.totals;
    const rates = data?.rates || {};
    const check = data?.sign_check;
    const operators = data?.operators || [];

    return (
        <section className="space-y-4">
            {/* Фильтр один и над всем: месяц базы; справа — когда спрашивали Sapar. */}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-2">
                    <CustomSelect
                        value={shownPeriod || data?.period || activePeriod}
                        onChange={(v) => onPeriodChange?.(v)}
                        options={periodOptions}
                        variant="ios"
                        className="min-w-0 flex-1 sm:w-64 sm:flex-none"
                        ariaLabel="Месяц базы"
                    />
                    <button type="button" onClick={load} disabled={loading} className={`${iosBtnGhost} shrink-0`} aria-label="Обновить" title="Обновить">
                        <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
                    </button>
                </div>
                <SignCheckLine check={check} />
            </div>

            {error && !data && <div className="rounded-xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700">{error}</div>}

            {!data && loading && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-[13px] text-slate-500`}>
                    <Loader2 size={15} className="animate-spin" /> Считаем показатели…
                </div>
            )}

            {data && t && (
                <div className={`space-y-4 transition-opacity ${loading ? 'opacity-60' : ''}`}>
                    {check && !check.configured && (
                        <Notice>Проверка подписания не настроена на сервере: нет доступа к Sapar. Успешки не считаются, пока его не подключат.</Notice>
                    )}
                    {t.without_iin > 0 && (
                        <Notice>
                            {num(t.without_iin)} {plural(t.without_iin, 'водитель', 'водителя', 'водителей')} без ИИН — подписание не проверяется.
                            Загрузите их заново файлом с колонкой iin.
                        </Notice>
                    )}
                    {check?.errors > 0 && (
                        <Notice tone="rose">
                            Sapar не ответил по {num(check.errors)} {plural(check.errors, 'водителю', 'водителям', 'водителям')} при
                            последней проверке — спросим снова в следующий прогон.
                        </Notice>
                    )}

                    {/* Плитки на белой карточке: серые плитки прямо на сером фоне
                        страницы не читаются как плитки. */}
                    <div className={`${iosCard} grid grid-cols-2 gap-2 p-2 sm:grid-cols-3 lg:grid-cols-5`}>
                        <Metric label="В базе" value={num(t.leads)} hint={data.period_label} />
                        <Metric
                            label="Обзвонили"
                            value={num(t.called)}
                            hint={`${pctText(rates.called)} базы`}
                            help="Водители, которым был хотя бы один звонок (кроме отменённых оператором до ответа)."
                        />
                        <Metric
                            label="Поговорили"
                            value={num(t.talked)}
                            hint={`≥10 с · ${pctText(rates.talked)} обзвоненных`}
                            help="Водители, с которыми был разговор не короче 10 секунд — только такой разговор может принести успешку."
                        />
                        <Metric
                            label="Успешки"
                            value={num(t.successes)}
                            tone={t.successes ? 'good' : null}
                            hint={t.signed ? `подписали всего — ${num(t.signed)}` : 'подписавших пока нет'}
                            help={RULES_HINT}
                        />
                        <Metric
                            label="Конверсия"
                            value={pctText(rates.conversion)}
                            hint="успешек из поговоривших"
                            help="Успешки, делённые на число водителей, с которыми поговорили не меньше 10 секунд."
                            helpAlign="right"
                        />
                    </div>

                    {/* min-w-0: иначе колонка сетки растягивается по неразрывному ряду
                        переключателей, и на телефоне карточки уезжают за край. */}
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 [&>*]:min-w-0">
                        <DocumentsCard totals={t} check={check} onOpen={onOpenJournal} />
                        <ByDayCard days={data.by_day || []} />
                    </div>

                    <PagedTable
                        title="Операторы"
                        help="По водителям базы выбранного месяца. Конверсия — успешки из водителей, с которыми оператор поговорил не меньше 10 секунд. Нажмите на строку — откроется журнал этого оператора."
                        rows={operators}
                        perPage={10}
                        empty="По этой базе ещё никто не звонил."
                        head={(
                            <tr>
                                <Th>Оператор</Th>
                                <Th right>Звонков</Th>
                                <Th right>Водителей</Th>
                                <Th right>Поговорили</Th>
                                <Th right>Время разговоров</Th>
                                <Th right>Успешки</Th>
                                <Th right>Конверсия</Th>
                            </tr>
                        )}
                        renderRow={(o) => (
                            <tr
                                key={o.operator_id}
                                className="cursor-pointer transition hover:bg-slate-50"
                                onClick={() => onOpenJournal?.({ operatorId: String(o.operator_id) })}
                            >
                                <Td><span className="whitespace-nowrap font-medium text-slate-800">{o.name}</span></Td>
                                <Td right>{num(o.attempts)}</Td>
                                <Td right>{num(o.leads_called)}</Td>
                                <Td right>{num(o.leads_talked)}</Td>
                                <Td right muted>{fmtTalk(o.talk_sec)}</Td>
                                <Td right><span className="font-semibold text-slate-900">{num(o.successes)}</span></Td>
                                <Td right>
                                    {o.leads_talked ? <Bar done={o.successes} total={o.leads_talked} tone="emerald" /> : <span className="text-slate-400">—</span>}
                                </Td>
                            </tr>
                        )}
                    />
                    {!data.is_active_period && (
                        <div className="px-1 text-[12px] text-slate-500">
                            Сейчас обзванивается база за {monthLabel(data.active_period)}.
                        </div>
                    )}
                </div>
            )}
        </section>
    );
};

export default DialListAnalytics;
