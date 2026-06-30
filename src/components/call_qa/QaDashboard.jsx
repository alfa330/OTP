import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from 'recharts';
import { ClipboardCheck, ListChecks, Gauge, Loader2, TrendingUp } from 'lucide-react';
import { APPLE_FONT, iosCard, IosBadge } from '../ui/ios';

/* Обзор — реальная статистика с /api/ai-qa/stats. Пустые места — честно «нет данных». */

const barColor = (v) => (v >= 85 ? '#10b981' : v >= 70 ? '#f59e0b' : '#f43f5e');
const TONE = { blue: 'bg-blue-50 text-blue-600', amber: 'bg-amber-50 text-amber-600', green: 'bg-emerald-50 text-emerald-600' };

export default function QaDashboard(props) {
    const { apiBaseUrl, withAccessTokenHeader } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [s, setS] = useState(null);

    useEffect(() => {
        if (!apiBaseUrl) { setS({ queue: null, evaluated: null, agreement: null, by_criterion: [] }); return; }
        let live = true;
        axios.get(`${apiBaseUrl}/api/ai-qa/stats`, { headers: headers() })
            .then((r) => { if (live) setS(r.data); })
            .catch(() => { if (live) setS({ queue: null, evaluated: null, agreement: null, by_criterion: [] }); });
        return () => { live = false; };
        // eslint-disable-next-line
    }, [apiBaseUrl]);

    if (!s) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className={`${iosCard} flex items-center justify-center gap-2 px-6 py-16 text-slate-400`}>
                <Loader2 size={20} className="animate-spin" />Загрузка…
            </div>
        );
    }

    const metrics = [
        { label: 'Оценено ИИ', value: s.evaluated ?? '—', Icon: ClipboardCheck, tone: 'blue' },
        { label: 'В очереди (7 дней)', value: s.queue ?? '—', Icon: ListChecks, tone: 'amber' },
        { label: 'Согласие с людьми', value: s.agreement != null ? `${s.agreement}%` : '—', Icon: Gauge, tone: 'green' },
    ];
    const chart = s.by_criterion || [];
    const weak = [...chart].sort((a, b) => a.v - b.v).slice(0, 3);

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {metrics.map((m) => (
                    <div key={m.label} className={`${iosCard} p-4`}>
                        <div className={`mb-2 grid h-9 w-9 place-items-center rounded-xl ${TONE[m.tone]}`}><m.Icon size={18} /></div>
                        <div className="text-[24px] font-semibold leading-none text-slate-900">{m.value}</div>
                        <div className="mt-1 text-[12px] text-slate-400">{m.label}</div>
                    </div>
                ))}
            </div>

            <div className={`${iosCard} p-4`}>
                <div className="mb-3 flex items-center justify-between">
                    <div className="text-[13px] font-semibold text-slate-700">Согласие ИИ↔человек по критериям</div>
                    <IosBadge tone="slate"><TrendingUp size={11} />по оценённым</IosBadge>
                </div>
                {chart.length === 0 ? (
                    <div className="flex flex-col items-center gap-1 py-10 text-center">
                        <p className="text-[13px] text-slate-500">Недостаточно данных</p>
                        <p className="text-[12px] text-slate-400">Появится, когда ИИ оценит звонки (вкладка «Оценки» → «Случайный звонок»).</p>
                    </div>
                ) : (
                    <>
                        <div className="h-64">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={chart} margin={{ top: 4, right: 8, left: -16, bottom: 4 }}>
                                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#94a3b8' }} interval={0} angle={-18} textAnchor="end" height={56} />
                                    <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: '#94a3b8' }} />
                                    <Tooltip cursor={{ fill: 'rgba(148,163,184,0.08)' }}
                                             contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }}
                                             formatter={(v) => [`${v}%`, 'согласие']} />
                                    <Bar dataKey="v" radius={[6, 6, 0, 0]}>
                                        {chart.map((d, i) => <Cell key={i} fill={barColor(d.v)} />)}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                        {weak.length > 0 && (
                            <div className="mt-3">
                                <div className="mb-1.5 text-[12px] font-semibold text-slate-600">Где ИИ чаще расходится с людьми</div>
                                <div className="space-y-1.5">
                                    {weak.map((w) => (
                                        <div key={w.name} className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2">
                                            <span className="text-[13px] text-slate-700">{w.name}</span>
                                            <IosBadge tone={w.v >= 70 ? 'amber' : 'red'}>{w.v}%</IosBadge>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
