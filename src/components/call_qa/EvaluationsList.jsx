import React, { useMemo, useState } from 'react';
import { Search, ChevronRight, Bot, User2, AlertTriangle } from 'lucide-react';
import { APPLE_FONT, iosCard, IosBadge } from '../ui/ios';

/* Список AI-оценок рядом с человеческими. Мок в форме API:
 * GET /api/ai-qa/evaluations?direction=&q=&status= */

const MOCK = [
    { id: 6434, direction: 'Основа', operator: 'Дарина С.', date: '29.06 14:12', ai: 92, human: 97, agreement: 83, status: 'reviewed' },
    { id: 6099, direction: 'Яндекс Регистрация', operator: 'Ильданов А.', date: '29.06 11:40', ai: null, human: 0, agreement: 38, status: 'queue' },
    { id: 6036, direction: 'Основа', operator: 'Роман К.', date: '29.06 12:15', ai: 78, human: 81, agreement: 67, status: 'queue' },
    { id: 6671, direction: 'Яндекс Регистрация', operator: 'Аман Т.', date: '29.06 09:05', ai: 95, human: null, agreement: null, status: 'ai_only' },
    { id: 6483, direction: 'Поток', operator: 'Нурлан Б.', date: '28.06 16:48', ai: 100, human: 100, agreement: 92, status: 'reviewed' },
];

const STATUS = {
    reviewed: { tone: 'green', label: 'Проверено' },
    queue:    { tone: 'amber', label: 'На ревью' },
    ai_only:  { tone: 'blue',  label: 'Только ИИ' },
};

const FILTERS = [{ k: 'all', l: 'Все' }, { k: 'queue', l: 'На ревью' }, { k: 'reviewed', l: 'Проверено' }, { k: 'ai_only', l: 'Только ИИ' }];

const Score = ({ Icon, value, tone }) => (
    <div className="flex items-center gap-1 text-[12.5px]">
        <Icon size={13} className="text-slate-300" />
        <span className={`font-semibold tabular-nums ${value == null ? 'text-slate-300' : 'text-slate-700'}`}>{value == null ? '—' : value}</span>
    </div>
);

export default function EvaluationsList(props) {
    const [q, setQ] = useState('');
    const [f, setF] = useState('all');

    const items = useMemo(() => MOCK.filter((m) =>
        (f === 'all' || m.status === f) &&
        (q.trim() === '' || `${m.id} ${m.operator} ${m.direction}`.toLowerCase().includes(q.toLowerCase()))
    ), [q, f]);

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative flex-1 min-w-[200px]">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск: # звонка, оператор, направление…"
                        className="w-full rounded-xl bg-slate-100 py-2.5 pl-9 pr-3 text-[13.5px] text-slate-800 placeholder-slate-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/60" />
                </div>
                <div className="flex flex-wrap gap-1.5">
                    {FILTERS.map((x) => (
                        <button key={x.k} onClick={() => setF(x.k)}
                            className={`rounded-lg px-3 py-2 text-[12.5px] font-semibold transition ${
                                f === x.k ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                            {x.l}
                        </button>
                    ))}
                </div>
            </div>

            <div className="space-y-2">
                {items.map((m) => {
                    const st = STATUS[m.status];
                    const lowAgree = m.agreement != null && m.agreement < 70;
                    return (
                        <button key={m.id} onClick={() => props.onOpen?.(m)}
                            className={`${iosCard} flex w-full items-center gap-3 p-3.5 text-left transition hover:ring-blue-200 active:scale-[0.995]`}>
                            <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                    <span className="text-[14px] font-semibold text-slate-900">#{m.id}</span>
                                    <IosBadge tone="slate">{m.direction}</IosBadge>
                                    <IosBadge tone={st.tone}>{st.label}</IosBadge>
                                </div>
                                <p className="mt-0.5 text-[12px] text-slate-400">{m.operator} · {m.date}</p>
                            </div>
                            <div className="flex shrink-0 items-center gap-3">
                                <Score Icon={Bot} value={m.ai} />
                                <Score Icon={User2} value={m.human} />
                                {m.agreement != null && (
                                    <IosBadge tone={lowAgree ? 'red' : m.agreement < 85 ? 'amber' : 'green'}>
                                        {lowAgree && <AlertTriangle size={11} />}{m.agreement}%
                                    </IosBadge>
                                )}
                                <ChevronRight size={16} className="text-slate-300" />
                            </div>
                        </button>
                    );
                })}
            </div>
            <p className="px-1 text-[11.5px] text-slate-400">
                <Bot size={12} className="mr-1 inline" />ИИ · <User2 size={12} className="mx-1 inline" />человек · % — согласие по transcript-критериям
            </p>
        </div>
    );
}
