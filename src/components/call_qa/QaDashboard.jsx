import React from 'react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from 'recharts';
import { ClipboardCheck, ListChecks, Gauge, ShieldAlert, TrendingUp } from 'lucide-react';
import { APPLE_FONT, iosCard, IosBadge } from '../ui/ios';

/* Обзор раздела. Данные — мок в форме API: GET /api/ai-qa/stats */

const METRICS = [
    { key: 'evaluated', label: 'Оценено сегодня', value: 642, Icon: ClipboardCheck, tone: 'blue' },
    { key: 'queue',     label: 'В очереди ревью', value: 38,  Icon: ListChecks,     tone: 'amber' },
    { key: 'agreement', label: 'Согласие с людьми', value: '79%', Icon: Gauge,      tone: 'green' },
    { key: 'critical',  label: 'Критич. на проверке', value: 5, Icon: ShieldAlert,  tone: 'red' },
];

// Согласие ИИ↔человек по критериям (по transcript-критериям)
const AGREEMENT = [
    { name: 'Приветствие', v: 71 },
    { name: 'Персонализация', v: 74 },
    { name: 'Идентификация', v: 88 },
    { name: 'Выявление потр.', v: 69 },
    { name: 'Презентация', v: 76 },
    { name: 'Возражения', v: 72 },
    { name: 'Грубость (КО)', v: 96 },
    { name: 'Достоверность (КО)', v: 90 },
    { name: 'Прощание', v: 83 },
];

const barColor = (v) => (v >= 85 ? '#10b981' : v >= 70 ? '#f59e0b' : '#f43f5e');

const TONE = {
    blue: 'bg-blue-50 text-blue-600', amber: 'bg-amber-50 text-amber-600',
    green: 'bg-emerald-50 text-emerald-600', red: 'bg-rose-50 text-rose-600',
};

export default function QaDashboard() {
    const weak = [...AGREEMENT].sort((a, b) => a.v - b.v).slice(0, 3);
    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            {/* Метрики */}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                {METRICS.map((m) => (
                    <div key={m.key} className={`${iosCard} p-4`}>
                        <div className={`mb-2 grid h-9 w-9 place-items-center rounded-xl ${TONE[m.tone]}`}>
                            <m.Icon size={18} />
                        </div>
                        <div className="text-[24px] font-semibold leading-none text-slate-900">{m.value}</div>
                        <div className="mt-1 text-[12px] text-slate-400">{m.label}</div>
                    </div>
                ))}
            </div>

            {/* График согласия */}
            <div className={`${iosCard} p-4`}>
                <div className="mb-3 flex items-center justify-between">
                    <div className="text-[13px] font-semibold text-slate-700">Согласие ИИ↔человек по критериям</div>
                    <IosBadge tone="slate"><TrendingUp size={11} />за 30 дней</IosBadge>
                </div>
                <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={AGREEMENT} margin={{ top: 4, right: 8, left: -16, bottom: 4 }}>
                            <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#94a3b8' }} interval={0} angle={-18} textAnchor="end" height={56} />
                            <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: '#94a3b8' }} />
                            <Tooltip cursor={{ fill: 'rgba(148,163,184,0.08)' }}
                                     contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }}
                                     formatter={(v) => [`${v}%`, 'согласие']} />
                            <Bar dataKey="v" radius={[6, 6, 0, 0]}>
                                {AGREEMENT.map((d, i) => <Cell key={i} fill={barColor(d.v)} />)}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
                <div className="mt-2 flex items-center gap-3 text-[11px] text-slate-400">
                    <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-emerald-500" />≥85% — в работе</span>
                    <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-amber-500" />70–84% — наблюдаем</span>
                    <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-rose-500" />&lt;70% — калибруем</span>
                </div>
            </div>

            {/* Проблемные критерии */}
            <div className={`${iosCard} p-4`}>
                <div className="mb-2 text-[13px] font-semibold text-slate-700">Где ИИ чаще расходится с людьми</div>
                <div className="space-y-1.5">
                    {weak.map((w) => (
                        <div key={w.name} className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2">
                            <span className="text-[13px] text-slate-700">{w.name}</span>
                            <IosBadge tone={w.v >= 70 ? 'amber' : 'red'}>{w.v}%</IosBadge>
                        </div>
                    ))}
                </div>
                <p className="mt-2 text-[11.5px] text-slate-400">Эти критерии — кандидаты на подстройку промпта и разборы (RAG).</p>
            </div>
        </div>
    );
}
