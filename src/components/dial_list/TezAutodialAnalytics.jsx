import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BarChart3, ChevronRight, Loader2, RefreshCw, Table2, TriangleAlert } from 'lucide-react';
import { Bar as RBar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { iosCard, iosGroupLabel, iosBtnGhost, IosHint, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { Bar, Metric, PagedTable, Td, Th } from '../wiki/reportKit';
import { buildPeriodOptions, monthLabel } from './dialListPeriods';
import { formatSeconds } from './tezAutodial';

/*
 * Вкладка «Аналитика» раздела «Автообзвон Тез» (контракт 15.10.2026, §5 analytics).
 *
 * Отдельный компонент, а не ветка внутри DialListAnalytics: у удалённого КЦ цель —
 * подпись документов (Sapar), у Тез — выход водителя на линию (TEZ APP), и у сервера
 * для профиля tez свой набор показателей. Каркас тот же: всё — по базе одного месяца,
 * плитки воронки, по дням, операторы; нажатие открывает журнал с готовым отбором.
 *
 * Порядок блоков — порядок вопросов руководителя Тез:
 *   1) плитки — объём / обработано / осталось / дозвонились / согласились / вышли на
 *      линию / конверсия;
 *   2) выход на линию — кто вышел после разговора, кто сам, кто уже работал при
 *      загрузке, кто обещал и не вышел (это и есть «дожать»);
 *   3) итоги — чем кончился последний разговор с водителем;
 *   4) по дням, операторы, офферы, звонки (автообзвоном и вручную) и WhatsApp.
 *
 * Объяснения — под «i» (правило владельца 25.08.2026: на экране числа и названия).
 * Цвет — со смыслом: зелёный у цели, цвет итога — из справочника итогов.
 */

const NF = new Intl.NumberFormat('ru-RU');
const num = (v) => NF.format(Number(v) || 0);
const pctText = (v) => (v == null ? '—' : `${String(v).replace('.', ',')}%`);
const share = (part, whole) => (Number(whole) ? Math.round((100 * (Number(part) || 0)) / Number(whole)) : 0);
const HEX_RE = /^#[0-9A-Fa-f]{6}$/;

const fmtTalk = (sec) => {
    const s = Math.max(0, Number(sec) || 0);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h) return `${h} ч ${m} мин`;
    if (m) return `${m} мин`;
    return s ? `${s} с` : '—';
};

const dayLabel = (iso) => {
    const [, m, d] = String(iso).split('-');
    return `${Number(d)}.${m}`;
};

const SUCCESS_HINT = 'Успешка — водитель вышел на линию (завершённый заказ в TEZ APP в месяце базы) после '
    + 'разговора с оператором не короче 10 секунд; засчитывается тому, кто говорил с ним последним до первого заказа. '
    + 'Вышел без такого разговора — «вышел сам», успешки нет.';

const Notice = ({ children }) => (
    <div className="flex items-start gap-2 rounded-xl bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-800 ring-1 ring-amber-100">
        <TriangleAlert size={14} className="mt-[1px] shrink-0" />
        <span>{children}</span>
    </div>
);

/* Строка-кнопка списка: подпись, число, доля; нажатие — журнал с этим отбором. */
const ListRow = ({ label, sub, value, whole, color, onClick, indent = false }) => (
    <li>
        <button
            type="button"
            onClick={onClick}
            disabled={!onClick || !value}
            className={`flex w-full items-center gap-3 py-2 text-left transition hover:bg-slate-50 disabled:cursor-default disabled:hover:bg-transparent ${indent ? 'pl-5' : ''}`}
        >
            {color && <span className={`${indent ? 'h-1.5 w-1.5 rounded-full' : 'h-2.5 w-2.5 rounded-[3px]'} shrink-0`} style={{ backgroundColor: color }} />}
            <span className="min-w-0 flex-1">
                <span className={`block truncate ${indent ? 'text-[12.5px] text-slate-600' : 'text-[13.5px] text-slate-800'}`}>{label}</span>
                {sub && <span className="block truncate text-[12px] text-slate-500">{sub}</span>}
            </span>
            <span className={`shrink-0 tabular-nums ${indent ? 'text-[12.5px] text-slate-700' : 'text-[13.5px] font-semibold text-slate-900'}`}>{num(value)}</span>
            {whole !== undefined && <span className="w-10 shrink-0 text-right text-[12px] tabular-nums text-slate-500">{share(value, whole)}%</span>}
            <ChevronRight size={14} className={`shrink-0 ${onClick && value ? 'text-slate-300' : 'text-transparent'}`} />
        </button>
    </li>
);

/* Выход на линию: из чего складывается цель и кого пора дожать. Нажатие открывает журнал только
   там, где у журнала есть ровно такой же отбор (успешки — фильтр «успешка»): число в строке и число
   строк в журнале обязаны совпадать. «Обещали и не вышли» и «перезвоны» считаются за всю базу, а
   этапы журнала «Обещали выйти» / «Перезвон назначен» — кто ждёт срока сейчас: они в полосе этапов. */
const GoalCard = ({ totals, onOpen }) => {
    const wentOnline = Number(totals.went_online) || 0;
    const successes = Number(totals.successes) || 0;
    const self = Math.max(0, wentOnline - successes);
    return (
        <section className="space-y-1.5">
            <div className="flex items-center gap-1.5">
                <span className={iosGroupLabel}>Выход на линию</span>
                <IosHint text={`${SUCCESS_HINT} Нажмите на строку успешек — откроется журнал с этими водителями. Кого дожать и кому перезвонить сейчас — этапы «Обещали выйти» и «Перезвон назначен» в журнале.`} />
            </div>
            <div className={`${iosCard} px-4 py-2`}>
                <ul className="divide-y divide-slate-100">
                    <ListRow label="Вышли после разговора — успешки" value={successes} color="#16a34a" onClick={() => onOpen?.({ sign: 'success' })} />
                    <ListRow label="Вышли сами, без разговора" value={self} />
                    <ListRow label="Уже работали при загрузке" value={totals.online_at_upload} />
                    <ListRow label="Обещали выйти и не вышли" sub="дожать" value={totals.promised_not_online} color="#eda100" />
                    <ListRow label="Назначали перезвон" value={totals.callbacks} />
                </ul>
            </div>
        </section>
    );
};

/* Итоги последнего разговора с водителем: цвет — из справочника итогов. */
const OutcomesCard = ({ items, onOpen }) => {
    const whole = items.reduce((sum, o) => sum + (Number(o.count) || 0), 0);
    return (
        <section className="space-y-1.5">
            <div className="flex items-center gap-1.5">
                <span className={iosGroupLabel}>Итоги</span>
                <IosHint text="Чем кончился последний разговор с водителем — по итогу, который поставил оператор. Нажмите на строку — откроется журнал с этими водителями." />
            </div>
            <div className={`${iosCard} px-4 py-2`}>
                {whole === 0 ? (
                    <div className="py-4 text-center text-[13px] text-slate-500">Итогов по этой базе ещё нет</div>
                ) : (
                    <ul className="divide-y divide-slate-100">
                        {items.filter((o) => Number(o.count) > 0).map((o) => {
                            const color = HEX_RE.test(o.color || '') ? o.color : '#8E8E93';
                            const subs = (Array.isArray(o.subtypes) ? o.subtypes : []).filter((s) => Number(s.count) > 0);
                            return (
                                <React.Fragment key={o.outcome_id || o.name}>
                                    <ListRow
                                        label={o.name}
                                        value={o.count}
                                        whole={whole}
                                        color={color}
                                        onClick={o.outcome_id ? () => onOpen?.({ outcomeId: String(o.outcome_id) }) : undefined}
                                    />
                                    {subs.map((s) => (
                                        <ListRow
                                            key={s.id || s.name}
                                            indent
                                            label={s.name}
                                            value={s.count}
                                            color={color}
                                            onClick={o.outcome_id && s.id
                                                ? () => onOpen?.({ outcomeId: String(o.outcome_id), outcomeSubtypeId: String(s.id) }) : undefined}
                                        />
                                    ))}
                                </React.Fragment>
                            );
                        })}
                    </ul>
                )}
            </div>
        </section>
    );
};

const METRICS = [
    { value: 'successes', label: 'Вышли на линию', name: 'Вышли на линию' },
    { value: 'agreements', label: 'Согласия', name: 'Согласий' },
    { value: 'talked', label: 'Разговоры ≥10 с', name: 'Разговоров ≥10 с' },
    { value: 'attempts', label: 'Звонки', name: 'Звонков' },
];
const CHART_COLOR = '#2a78d6';

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

/* По дням — один показатель за раз (одна ось, один цвет), рядом таблица. */
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
                    <IosHint text="Звонки, разговоры и согласия — по дню звонка; вышли на линию — по дню первого заказа водителя. Всё — по водителям базы выбранного месяца." />
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
                    <div className="max-h-72 overflow-auto">
                        <table className="w-full border-collapse">
                            <thead className="sticky top-0 border-b border-slate-100 bg-white">
                                <tr><Th>День</Th><Th right>Звонки</Th><Th right>Разговоры ≥10 с</Th><Th right>Согласия</Th><Th right>Вышли</Th></tr>
                            </thead>
                            <tbody className="divide-y divide-slate-50">
                                {[...days].reverse().map((d) => (
                                    <tr key={d.day}>
                                        <Td>{dayLabel(d.day)}</Td>
                                        <Td right>{num(d.attempts)}</Td>
                                        <Td right>{num(d.talked)}</Td>
                                        <Td right>{num(d.agreements)}</Td>
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

/**
 * Показатели — по готовому ответу сервера (вынесено из загрузки, чтобы проверять
 * разметку тестом без сети). data — ответ GET …/analytics у профиля tez.
 */
export const TezAnalyticsBody = ({ data, onOpenJournal }) => {
    const t = data?.totals || {};
    const rates = data?.rates || {};
    const operators = Array.isArray(data?.operators) ? data.operators : [];
    const offers = Array.isArray(data?.offers) ? data.offers : [];
    const byOutcome = Array.isArray(data?.by_outcome) ? data.by_outcome : [];
    const wa = data?.whatsapp || {};
    const attempts = Number(t.attempts) || 0;
    const auto = Number(t.auto_attempts) || 0;
    return (
        <div className="space-y-4">
            {/* Плитки на белой карточке: серые плитки прямо на сером фоне страницы
                не читаются как плитки. */}
            <div className={`${iosCard} grid grid-cols-2 gap-2 p-2 sm:grid-cols-4 lg:grid-cols-7`}>
                <Metric label="Объём" value={num(t.leads)} hint={data?.period_label} help="Водители в базе месяца, без тестовых номеров." />
                <Metric
                    label="Обработано"
                    value={num(t.processed)}
                    hint={`${share(t.processed, t.leads)}% базы`}
                    help="Обзвон по водителю закончен: вышел на линию, дозвонились без перезвона, кончились попытки или руководитель исключил."
                />
                <Metric
                    label="Осталось"
                    value={num(t.remaining)}
                    hint={`${share(t.remaining, t.leads)}% базы`}
                    help="Ещё в обзвоне: новые и те, кому уже звонили, — пока не вышли на линию."
                />
                <Metric
                    label="Дозвонились"
                    value={num(t.answered)}
                    hint={`поговорили ≥10 с — ${num(t.talked)}`}
                    help="Водители, которые хотя бы раз ответили. «Поговорили» — разговор не короче 10 секунд: только он может принести успешку."
                />
                <Metric
                    label="Согласились"
                    value={num(t.agreements)}
                    hint={`${pctText(rates.agreement)} поговоривших`}
                    help="Водители с итогом «согласен выйти на линию» — итог с датой выхода."
                />
                <Metric
                    label="Вышли на линию"
                    value={num(t.went_online)}
                    tone={t.went_online ? 'good' : null}
                    hint={`успешек — ${num(t.successes)}`}
                    help={`Вышли на линию после загрузки базы. ${SUCCESS_HINT}`}
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
                <GoalCard totals={t} onOpen={onOpenJournal} />
                <OutcomesCard items={byOutcome} onOpen={onOpenJournal} />
            </div>

            <ByDayCard days={Array.isArray(data?.by_day) ? data.by_day : []} />

            <PagedTable
                title="Операторы"
                help="По водителям базы выбранного месяца. «Авто» — звонки, которые телефон сделал сам в статусе «Активный»; остальные оператор набрал сам в статусе «Исход». Конверсия — успешки из водителей, с которыми оператор поговорил не меньше 10 секунд. Просрочки — сколько раз не успел с итогом за постобработку. Нажмите на строку — откроется журнал этого оператора."
                rows={operators}
                perPage={10}
                empty="По этой базе ещё никто не звонил."
                head={(
                    <tr>
                        <Th>Оператор</Th>
                        <Th right>Звонков</Th>
                        <Th right>Авто</Th>
                        <Th right>Водителей</Th>
                        <Th right>Поговорили</Th>
                        <Th right>Время</Th>
                        <Th right>Согласия</Th>
                        <Th right>Перезвоны</Th>
                        <Th right>Вышли</Th>
                        <Th right>Конверсия</Th>
                        <Th right>Просрочки</Th>
                        <Th right>Постобработка</Th>
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
                        <Td right muted>{num(o.auto_attempts)}</Td>
                        <Td right>{num(o.leads_called)}</Td>
                        <Td right>{num(o.talked)}</Td>
                        <Td right muted>{fmtTalk(o.talk_sec)}</Td>
                        <Td right>{num(o.agreements)}</Td>
                        <Td right>{num(o.callbacks)}</Td>
                        <Td right><span className="font-semibold text-slate-900">{num(o.successes)}</span></Td>
                        <Td right>
                            {o.talked ? <Bar done={o.successes} total={o.talked} tone="emerald" /> : <span className="text-slate-400">—</span>}
                        </Td>
                        <Td right>
                            {Number(o.wrapup_expired) > 0
                                ? <span className="font-medium text-amber-600">{num(o.wrapup_expired)}</span>
                                : <span className="text-slate-400">0</span>}
                        </Td>
                        <Td right muted>{o.avg_wrapup_sec == null ? '—' : formatSeconds(o.avg_wrapup_sec)}</Td>
                    </tr>
                )}
            />

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 [&>*]:min-w-0">
                <PagedTable
                    title="Акции и офферы"
                    help="Сколько раз оператор отметил оффер в окне итога, сколько из этих водителей согласились выйти на линию и сколько вышли."
                    rows={offers}
                    perPage={5}
                    empty="Офферы в этой базе ещё не предлагали."
                    head={(
                        <tr>
                            <Th>Оффер</Th>
                            <Th right>Предложили</Th>
                            <Th right>Согласились</Th>
                            <Th right>Вышли</Th>
                        </tr>
                    )}
                    renderRow={(o) => (
                        <tr key={o.offer_id || o.title}>
                            <Td><span className="font-medium text-slate-800">{o.title}</span></Td>
                            <Td right>{num(o.offered)}</Td>
                            <Td right>{num(o.agreements)}</Td>
                            <Td right><span className="font-semibold text-slate-900">{num(o.successes)}</span></Td>
                        </tr>
                    )}
                />
                <section className="space-y-1.5">
                    <div className="flex items-center gap-1.5">
                        <span className={iosGroupLabel}>Звонки и сообщения</span>
                        <IosHint text="Звонки по водителям базы месяца: автообзвоном — телефон звонил сам в статусе «Активный», вручную — оператор сам в статусе «Исход». Просрочки постобработки — оператор не успел с итогом, и телефон поставил «Офлайн». WhatsApp — сообщения по шаблонам из карточки звонка." />
                    </div>
                    <div className={`${iosCard} grid grid-cols-2 gap-2 p-2`}>
                        <Metric label="Звонков" value={num(attempts)} hint={`авто ${num(auto)} · вручную ${num(Math.max(0, attempts - auto))}`} />
                        <Metric label="Просрочки постобработки" value={num(t.wrapup_expired)} tone={Number(t.wrapup_expired) > 0 ? 'warn' : null} />
                        <Metric label="WhatsApp отправлено" value={num(wa.sent ?? t.wa_sent)} />
                        <Metric label="WhatsApp не ушло" value={num(wa.failed)} tone={Number(wa.failed) > 0 ? 'bad' : null} />
                    </div>
                </section>
            </div>
        </div>
    );
};

const TezAutodialAnalytics = ({
    apiBaseUrl, authHeaders, departmentId, periods = [], activePeriod = '', period = '', onPeriodChange, onOpenJournal,
}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const seq = useRef(0);

    // 'all' — режим журнала; аналитика всегда про один месяц.
    const shownPeriod = period && period !== 'all' ? period : '';

    const load = useCallback(async () => {
        const my = ++seq.current;
        setLoading(true);
        setError('');
        try {
            const qs = shownPeriod ? `?period=${encodeURIComponent(shownPeriod)}` : '';
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/analytics${qs}`, {
                credentials: 'include', headers: authHeaders(),
            });
            const body = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(body?.error || `HTTP ${resp.status}`);
            if (my === seq.current) setData(body);
        } catch (e) {
            if (my === seq.current) setError(e.message || 'Не удалось загрузить аналитику');
        } finally {
            if (my === seq.current) setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, departmentId, shownPeriod]);

    useEffect(() => { load(); }, [load]);

    const periodOptions = useMemo(
        () => buildPeriodOptions(periods, { activePeriod, grouped: true }),
        [periods, activePeriod],
    );

    // Сервер, который профиль tez ещё не знает, отдал бы аналитику удалённого КЦ
    // (документы Sapar) — её показывать нельзя: у Тез таких чисел нет.
    const ready = data && data.profile === 'tez' && data.totals;

    return (
        <section className="space-y-4">
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

            {error && !data && <div className="rounded-xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700">{error}</div>}

            {!data && loading && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-[13px] text-slate-500`}>
                    <Loader2 size={15} className="animate-spin" /> Считаем показатели…
                </div>
            )}

            {data && !ready && !loading && (
                <Notice>Сервер ещё не считает аналитику автообзвона Тез — показатели появятся после его обновления.</Notice>
            )}

            {ready && (
                <div className={`space-y-4 transition-opacity ${loading ? 'opacity-60' : ''}`}>
                    <TezAnalyticsBody data={data} onOpenJournal={onOpenJournal} />
                    {data.is_active_period === false && data.active_period && (
                        <div className="px-1 text-[12px] text-slate-500">
                            Сейчас обзванивается база за {monthLabel(data.active_period)}.
                        </div>
                    )}
                </div>
            )}
        </section>
    );
};

export default TezAutodialAnalytics;
