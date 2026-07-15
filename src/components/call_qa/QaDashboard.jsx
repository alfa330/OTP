import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from 'recharts';
import {
    ClipboardCheck, ListChecks, Loader2, Siren, Crosshair, ShieldCheck,
    UserCheck, Wrench, Grid3X3, Gauge, Info, Database, Timer, Layers3, Activity,
    AlertCircle, RefreshCw,
} from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnSecondary, IosBadge } from '../ui/ios';

/* Обзор — метрики доверия (реальные данные с /api/ai-qa/stats).
 * Три вопроса вместо одной цифры «согласие»:
 *   1) Точность тревог — когда ИИ ставит «Неверно», как часто человек согласен?
 *   2) Полнота — какую долю человеческих нарушений ИИ ловит?
 *   3) Надёжность зачёта — когда ИИ ставит «Верно», можно ли верить?
 * «Где отрабатывать» — критерии с наибольшим числом ложных тревог и пропусков:
 * туда направлять разборы эксперта. Пустые места — честно «—», без выдуманных цифр. */

const barColor = (v) => (v >= 85 ? '#10b981' : v >= 70 ? '#f59e0b' : '#f43f5e');
const rateTone = (pct, good, ok) => (pct == null ? 'slate' : pct >= good ? 'green' : pct >= ok ? 'amber' : 'red');
const TONE = {
    blue: 'bg-blue-50 text-blue-600', amber: 'bg-amber-50 text-amber-600',
    green: 'bg-emerald-50 text-emerald-600', red: 'bg-rose-50 text-rose-600',
    slate: 'bg-slate-100 text-slate-500',
};

/* Большая плитка «вопрос доверия»: процент + из чего он посчитан. */
function RateTile({ label, hint, rate, good, ok, Icon }) {
    const pct = rate?.pct ?? null;
    return (
        <div className={`${iosCard} p-4`}>
            <div className={`mb-2 grid h-9 w-9 place-items-center rounded-xl ${TONE[rateTone(pct, good, ok)]}`}>
                <Icon size={18} />
            </div>
            <div className="text-[24px] font-semibold leading-none text-slate-900">{pct != null ? `${pct}%` : '—'}</div>
            <div className="mt-1 text-[12.5px] font-medium text-slate-600">{label}</div>
            <div className="mt-0.5 text-[11.5px] leading-snug text-slate-400">
                {rate ? `${rate.hits} из ${rate.total} · ${hint}` : hint}
            </div>
        </div>
    );
}

function SmallStat({ label, value, Icon, tone = 'blue' }) {
    return (
        <div className={`${iosCard} flex items-center gap-3 px-4 py-3`}>
            <div className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${TONE[tone]}`}><Icon size={16} /></div>
            <div className="min-w-0">
                <div className="text-[17px] font-semibold leading-none text-slate-900">{value ?? '—'}</div>
                <div className="mt-0.5 truncate text-[11.5px] text-slate-400">{label}</div>
            </div>
        </div>
    );
}

const V_LABEL = { Correct: 'Верно', Incorrect: 'Неверно', 'N/A': 'N/A' };
const VERDICTS = ['Correct', 'Incorrect', 'N/A'];

/* Матрица «человек × ИИ»: диагональ — согласие; колонка «Неверно» без согласия
 * человека — ложные тревоги; строка «Неверно» мимо ИИ — пропуски. */
function Matrix({ matrix, deficiency }) {
    const cellCls = (h, a) => (h === a ? 'bg-emerald-50 font-semibold text-emerald-700'
        : a === 'Incorrect' ? 'bg-rose-50 text-rose-700'
        : h === 'Incorrect' ? 'bg-amber-50 text-amber-700' : 'bg-slate-50 text-slate-500');
    const cellTitle = (h, a) => (h === a ? 'согласие'
        : a === 'Incorrect' ? 'ложная тревога: ИИ видит нарушение, человек — нет'
        : h === 'Incorrect' ? 'пропуск: человек видит нарушение, ИИ — нет' : 'расхождение');
    return (
        <div className={`${iosCard} p-4`}>
            <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-slate-700">
                <Grid3X3 size={15} className="text-slate-400" />Матрица вердиктов: человек × ИИ
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-center text-[12.5px]">
                    <caption className="sr-only">Сравнение решений человека и ИИ по каждому вердикту</caption>
                    <thead>
                        <tr className="text-[11px] uppercase tracking-wide text-slate-400">
                            <th scope="col" className="py-1.5 pr-2 text-left font-medium">Человек ↓ · ИИ →</th>
                            {VERDICTS.map((v) => <th scope="col" key={v} className="px-2 py-1.5 font-medium">{V_LABEL[v]}</th>)}
                        </tr>
                    </thead>
                    <tbody>
                        {VERDICTS.map((h) => (
                            <tr key={h}>
                                <th scope="row" className="py-1 pr-2 text-left font-medium text-slate-600">{V_LABEL[h]}</th>
                                {VERDICTS.map((a) => (
                                    <td key={a} className="p-0.5">
                                        <div title={cellTitle(h, a)} aria-label={`${V_LABEL[h]} у человека, ${V_LABEL[a]} у ИИ: ${matrix?.[h]?.[a] ?? 0}, ${cellTitle(h, a)}`}
                                             className={`rounded-lg px-2 py-1.5 tabular-nums ${cellCls(h, a)}`}>
                                            {matrix?.[h]?.[a] ?? 0}
                                        </div>
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
                <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-emerald-300" />согласие</span>
                <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-rose-300" />ложная тревога</span>
                <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-amber-300" />пропуск</span>
                {deficiency > 0 && (
                    <span className="flex items-center gap-1"><Info size={12} />
                        {deficiency} «недочёт(а)» человека не учтены — у ИИ нет вердикта Deficiency
                    </span>
                )}
            </div>
        </div>
    );
}

/* «Где отрабатывать»: критерии, дающие ложные тревоги/пропуски. Разбор эксперта
 * по критерию с 20 ложными тревогами даст больше, чем по критерию с одной. */
function FocusTable({ rows }) {
    return (
        <div className={`${iosCard} p-4`}>
            <div className="mb-1 flex items-center gap-2 text-[13px] font-semibold text-slate-700">
                <Wrench size={15} className="text-slate-400" />Где отрабатывать
            </div>
            <p className="mb-3 text-[11.5px] text-slate-400">
                Критерии с расхождениями — сюда направлять разборы. «Правил» — сколько прецедентов уже сохранено.
            </p>
            {(!rows || rows.length === 0) ? (
                <div className="py-6 text-center text-[12.5px] text-slate-400">Расхождений нет — не с чем работать 🎉</div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-[12.5px]">
                        <caption className="sr-only">Критерии с наибольшим числом расхождений</caption>
                        <thead>
                            <tr className="text-left text-[11px] uppercase tracking-wide text-slate-400">
                                <th scope="col" className="py-1.5 pr-2 font-medium">Критерий</th>
                                <th scope="col" className="px-2 py-1.5 text-right font-medium" title="Сколько раз ИИ поставил «Неверно»">Тревог</th>
                                <th scope="col" className="px-2 py-1.5 text-right font-medium" title="Тревоги, где человек не увидел нарушения">Ложных</th>
                                <th scope="col" className="px-2 py-1.5 text-right font-medium" title="Нарушения человека, которые ИИ не увидел">Пропусков</th>
                                <th scope="col" className="px-2 py-1.5 text-right font-medium" title="Сохранённых разборов по критерию">Правил</th>
                                <th scope="col" className="py-1.5 pl-2 text-right font-medium">Точность тревог</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r) => {
                                const prec = r.alarms ? Math.round((100 * r.alarm_hits) / r.alarms) : null;
                                return (
                                    <tr key={`${r.direction}·${r.name}`} className="border-t border-slate-100">
                                        <td className="max-w-[260px] py-2 pr-2">
                                            <div className="truncate font-medium text-slate-700" title={r.name}>{r.name}</div>
                                            <div className="text-[11px] text-slate-400">{r.direction} · {r.n} сверок</div>
                                        </td>
                                        <td className="px-2 py-2 text-right tabular-nums text-slate-600">{r.alarms}</td>
                                        <td className="px-2 py-2 text-right tabular-nums font-semibold text-rose-600">{r.false_alarms || '—'}</td>
                                        <td className="px-2 py-2 text-right tabular-nums font-semibold text-amber-600">{r.misses || '—'}</td>
                                        <td className="px-2 py-2 text-right tabular-nums text-slate-600">{r.rules ?? 0}</td>
                                        <td className="py-2 pl-2 text-right">
                                            <IosBadge tone={rateTone(prec, 70, 40)}>{prec != null ? `${prec}%` : '—'}</IosBadge>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

/* Чистый эталон: итоги ревью (человек реально смотрел карточку). */
function ReviewedBlock({ r }) {
    const total = r ? r.confirmed + r.adjudicated : 0;
    return (
        <div className={`${iosCard} p-4`}>
            <div className="mb-1 flex items-center gap-2 text-[13px] font-semibold text-slate-700">
                <UserCheck size={15} className="text-slate-400" />Чистый эталон — по итогам ревью
            </div>
            {!r ? (
                <p className="py-4 text-[12.5px] text-slate-400">Появится после обновления сервера (миграция меты).</p>
            ) : total === 0 ? (
                <p className="py-4 text-[12.5px] leading-relaxed text-slate-400">
                    Пока нет проверенных ревью. Открывайте звонки из «Очереди ревью» и нажимайте
                    «Подтвердить» или «Сохранить разбор» — здесь появится точность тревог, посчитанная
                    только по звонкам, на которые человек реально посмотрел.
                </p>
            ) : (
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2 py-1 text-[12.5px] text-slate-600">
                    <span>Проверено ревью: <b className="tabular-nums">{total}</b>
                        <span className="text-slate-400"> (подтверждено {r.confirmed}, с исправлениями {r.adjudicated})</span>
                    </span>
                    <span>Вердиктов одобрено: <b className="tabular-nums text-emerald-600">{r.endorsed}</b></span>
                    <span>Исправлено: <b className="tabular-nums text-rose-600">{r.corrected}</b></span>
                    <span className="flex items-center gap-1.5">Точность тревог по ревью:
                        <IosBadge tone={rateTone(r.alarm_precision?.pct, 70, 40)}>
                            {r.alarm_precision ? `${r.alarm_precision.pct}% (${r.alarm_precision.hits}/${r.alarm_precision.total})` : 'тревог не было'}
                        </IosBadge>
                    </span>
                </div>
            )}
        </div>
    );
}

export default function QaDashboard(props) {
    const { apiBaseUrl, withAccessTokenHeader } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [s, setS] = useState(null);
    const [error, setError] = useState(null);
    const requestRef = useRef({ id: 0, controller: null });

    const load = () => {
        requestRef.current.controller?.abort();
        const controller = new AbortController();
        const requestId = requestRef.current.id + 1;
        requestRef.current = { id: requestId, controller };
        setS(null); setError(null);
        if (!apiBaseUrl) {
            setError('Сервис статистики не настроен');
            return;
        }
        axios.get(`${apiBaseUrl}/api/ai-qa/stats`, { headers: headers(), signal: controller.signal })
            .then((response) => {
                if (requestId === requestRef.current.id) setS(response.data);
            })
            .catch((requestError) => {
                if (!axios.isCancel(requestError) && requestId === requestRef.current.id) {
                    setError(requestError?.response?.data?.error || 'Не удалось загрузить статистику');
                }
            });
    };

    useEffect(() => {
        load();
        return () => requestRef.current.controller?.abort();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl]);

    if (!s && !error) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className={`${iosCard} flex items-center justify-center gap-2 px-6 py-16 text-slate-500`} role="status">
                <Loader2 size={20} className="animate-spin" aria-hidden="true" />Загрузка статистики…
            </div>
        );
    }

    if (error) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`} role="alert">
                <AlertCircle size={27} className="text-rose-500" />
                <div>
                    <p className="text-[14px] font-semibold text-slate-800">Статистика недоступна</p>
                    <p className="mt-1 text-[12.5px] text-slate-500">{error}</p>
                </div>
                <button type="button" onClick={load} className={iosBtnSecondary}><RefreshCw size={14} />Повторить</button>
            </div>
        );
    }

    // Худшие первыми (backend сортирует по возрастанию согласия); 16 — чтобы подписи читались.
    const chart = (s.by_criterion || []).slice(0, 16);
    const reviewedTotal = s.reviewed ? s.reviewed.confirmed + s.reviewed.adjudicated : null;
    const rag = s.rag || null;
    const rolloutLabel = rag?.rollout?.length
        ? rag.rollout.map((item) => `${item.direction_id}: ${item.mode}${item.mode === 'canary' ? ` ${item.canary_percent}%` : ''}`).join(' · ')
        : rag?.status === 'error' ? 'данные недоступны' : 'настройки не переданы';

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <RateTile label="Точность тревог" Icon={Siren} rate={s.alarm_precision} good={70} ok={40}
                          hint="«Неверно» от ИИ, с которыми человек согласен" />
                <RateTile label="Полнота" Icon={Crosshair} rate={s.recall} good={70} ok={40}
                          hint="нарушений человека, которые ИИ поймал" />
                <RateTile label="Надёжность «Верно»" Icon={ShieldCheck} rate={s.correct_reliability} good={95} ok={85}
                          hint="зачётов ИИ, с которыми человек согласен" />
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <SmallStat label="Оценено ИИ" value={s.evaluated ?? '—'} Icon={ClipboardCheck} tone="blue" />
                <SmallStat label="В очереди ревью" value={s.queue ?? '—'} Icon={ListChecks} tone="amber" />
                <SmallStat label="Проверено человеком" value={reviewedTotal ?? '—'} Icon={UserCheck} tone="green" />
                <SmallStat label="Общее согласие" value={s.agreement != null ? `${s.agreement}%` : '—'} Icon={Gauge} tone="slate" />
            </div>

            <div className={`${iosCard} p-4`}>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <div className="flex items-center gap-2 text-[13px] font-semibold text-slate-700">
                            <Database size={15} className="text-slate-400" />Наблюдаемость RAG · 30 дней
                        </div>
                        <p className="mt-0.5 text-[11.5px] text-slate-400">Режим раскатки: {rolloutLabel}</p>
                    </div>
                    <IosBadge tone={rag?.status === 'ready' ? 'green' : rag?.status === 'degraded' ? 'amber' : 'slate'}>
                        {rag?.status === 'ready' ? 'trace готов' : rag?.status === 'degraded' ? 'есть degraded runs' : 'нет данных'}
                    </IosBadge>
                </div>
                <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
                    <SmallStat label="Retrieval-запусков" value={rag?.runs ?? '—'} Icon={Activity} tone="blue" />
                    <SmallStat label="Правил вошло / найдено" value={rag ? `${rag.included ?? 0} / ${rag.retrieved ?? 0}` : '—'} Icon={Layers3} tone="green" />
                    <SmallStat label="Retrieval p50 / p95" value={rag?.retrieval_p50_ms != null ? `${rag.retrieval_p50_ms} / ${rag.retrieval_p95_ms ?? '—'} мс` : '—'} Icon={Timer} tone="slate" />
                    <SmallStat label="Устаревших оценок" value={rag?.stale_evaluations ?? '—'} Icon={Database}
                               tone={(rag?.stale_evaluations || 0) > 0 ? 'amber' : 'green'} />
                </div>
                {rag?.degraded_runs > 0 && (
                    <p className="mt-2 text-[11.5px] text-amber-700">
                        Degraded retrieval: {rag.degraded_runs}. Эти оценки выполнены без подстановки случайных fallback-правил.
                    </p>
                )}
            </div>

            <FocusTable rows={s.focus} />

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Matrix matrix={s.matrix} deficiency={s.deficiency || 0} />
                <ReviewedBlock r={s.reviewed} />
            </div>

            <div className={`${iosCard} p-4`}>
                <div className="mb-3 flex items-center justify-between">
                    <div className="text-[13px] font-semibold text-slate-700">Согласие ИИ↔человек по критериям</div>
                    <IosBadge tone="slate">худшие {chart.length} · ≥3 сверок</IosBadge>
                </div>
                {chart.length === 0 ? (
                    <div className="flex flex-col items-center gap-1 py-10 text-center">
                        <p className="text-[13px] text-slate-500">Недостаточно данных</p>
                        <p className="text-[12px] text-slate-400">Появится, когда ИИ оценит звонки (вкладка «Оценки» → «Случайный звонок»).</p>
                    </div>
                ) : (
                    <div className="h-64" role="img" aria-label="Согласие ИИ и человека по критериям; подробные значения перечислены после графика">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chart} margin={{ top: 4, right: 8, left: -16, bottom: 4 }}>
                                <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#94a3b8' }} interval={0} angle={-18} textAnchor="end" height={56} />
                                <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: '#94a3b8' }} />
                                <Tooltip cursor={{ fill: 'rgba(148,163,184,0.08)' }}
                                         contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }}
                                         formatter={(v) => [`${v}%`, 'согласие']}
                                         labelFormatter={(name, payload) => {
                                             const row = payload?.[0]?.payload;
                                             return row ? `${name} · ${row.direction} · ${row.n} сверок` : name;
                                         }} />
                                <Bar dataKey="v" radius={[6, 6, 0, 0]}>
                                    {chart.map((d, i) => <Cell key={i} fill={barColor(d.v)} />)}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                        <ul className="sr-only">
                            {chart.map((item) => <li key={`${item.direction}-${item.name}`}>{item.name}, {item.direction}: {item.v}% согласия, {item.n} сверок</li>)}
                        </ul>
                    </div>
                )}
            </div>
        </div>
    );
}
