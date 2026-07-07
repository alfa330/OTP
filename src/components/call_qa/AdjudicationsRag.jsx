import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Search, Database, Check, X, Minus, ArrowRight, Quote, Repeat, Loader2 } from 'lucide-react';
import { APPLE_FONT, iosCard, IosBadge } from '../ui/ios';

/* База разборов (RAG) — реальные данные с GET /api/ai-qa/adjudications. */

const V = { Correct: { t: 'green', l: 'Верно', I: Check }, Incorrect: { t: 'red', l: 'Неверно', I: X }, 'N/A': { t: 'slate', l: 'N/A', I: Minus } };
const Verdict = ({ v }) => { const m = V[v] || V['N/A']; return <IosBadge tone={m.t}><m.I size={11} />{m.l}</IosBadge>; };

export default function AdjudicationsRag(props) {
    const { apiBaseUrl, withAccessTokenHeader } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [q, setQ] = useState('');
    const [dir, setDir] = useState('all');
    const [data, setData] = useState(null);   // null = загрузка

    useEffect(() => {
        if (!apiBaseUrl) { setData([]); return; }
        let live = true;
        axios.get(`${apiBaseUrl}/api/ai-qa/adjudications`, { headers: headers() })
            .then((r) => { if (live) setData(r.data.items || []); })
            .catch(() => { if (live) setData([]); });
        return () => { live = false; };
        // eslint-disable-next-line
    }, [apiBaseUrl]);

    const dirs = useMemo(() => ['all', ...Array.from(new Set((data || []).map((m) => m.direction)))], [data]);
    const items = useMemo(() => (data || []).filter((m) =>
        (dir === 'all' || m.direction === dir) &&
        (q.trim() === '' || `${m.criterion} ${m.excerpt} ${m.reason}`.toLowerCase().includes(q.toLowerCase()))
    ), [q, dir, data]);

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative flex-1 min-w-[220px]">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по критерию, ситуации, правилу…"
                        className="w-full rounded-xl bg-slate-100 py-2.5 pl-9 pr-3 text-[13.5px] text-slate-800 placeholder-slate-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/60" />
                </div>
                <div className="flex flex-wrap gap-1.5">
                    {dirs.map((d) => (
                        <button key={d} onClick={() => setDir(d)}
                            className={`rounded-lg px-3 py-2 text-[12.5px] font-semibold transition ${
                                dir === d ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                            {d === 'all' ? 'Все' : d}
                        </button>
                    ))}
                </div>
            </div>

            {data === null ? (
                <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-12 text-slate-400`}>
                    <Loader2 size={20} className="animate-spin" />Загрузка…
                </div>
            ) : items.length === 0 ? (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <Database size={26} className="text-slate-300" />
                    <p className="text-[13px] text-slate-500">Разборов пока нет</p>
                    <p className="text-[12px] text-slate-400">Они появятся, когда проверяющий исправит оценку ИИ в карточке ревью.</p>
                </div>
            ) : (
                <>
                    <div className="px-1 text-[12px] text-slate-400">{items.length} разбор(ов) · подтягиваются в оценки по смыслу</div>
                    <div className="space-y-2.5">
                        {items.map((m) => (
                            <div key={m.id} className={`${iosCard} p-4`}>
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="flex flex-wrap items-center gap-1.5">
                                            <span className="text-[13.5px] font-semibold text-slate-800">{m.criterion}</span>
                                            <IosBadge tone="slate">{m.direction}</IosBadge>
                                        </div>
                                        <div className="mt-1.5 flex items-center gap-1.5">
                                            <Verdict v={m.ai} />
                                            <ArrowRight size={13} className="text-slate-300" />
                                            <Verdict v={m.correct} />
                                            <span className="text-[11px] text-slate-400">(было → стало правильно)</span>
                                        </div>
                                    </div>
                                    <IosBadge tone="blue"><Repeat size={11} />{m.use_count}</IosBadge>
                                </div>
                                {m.situation && (
                                    <p className="mt-2 text-[12.5px] text-slate-600"><b className="text-slate-500">Когда применять:</b> {m.situation}</p>
                                )}
                                {m.excerpt && (
                                    <p className="mt-2 flex gap-1.5 rounded-lg bg-slate-50 px-3 py-2 text-[12.5px] italic text-slate-600 ring-1 ring-slate-100">
                                        <Quote size={13} className="mt-0.5 shrink-0 text-slate-300" />{m.excerpt}
                                    </p>
                                )}
                                <p className="mt-2 text-[13px] text-slate-700"><b className="text-slate-500">Правило:</b> {m.reason}</p>
                                {m.not_covered && (
                                    <p className="mt-1 text-[12.5px] text-rose-600/80"><b className="text-rose-500/70">НЕ оправдывает:</b> {m.not_covered}</p>
                                )}
                                <p className="mt-1.5 text-[11px] text-slate-400">{m.by} · {m.date}</p>
                            </div>
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}
