import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Search, Database, Check, X, Minus, ArrowRight, Pencil, Trash2, Quote, Repeat, Loader2 } from 'lucide-react';
import { APPLE_FONT, iosCard, IosBadge, iosBtnGhost } from '../ui/ios';

/* База разборов (RAG) — «память ИИ». Просмотр/поиск/правка.
 * Данные — мок в форме API: GET /api/ai-qa/adjudications?direction=&criterion=&q= */

const V = { Correct: { t: 'green', l: 'Верно', I: Check }, Incorrect: { t: 'red', l: 'Неверно', I: X }, 'N/A': { t: 'slate', l: 'N/A', I: Minus } };
const Verdict = ({ v }) => { const m = V[v] || V['N/A']; return <IosBadge tone={m.t}><m.I size={11} />{m.l}</IosBadge>; };

const MOCK = [
    { id: 1, direction: 'Основа', criterion: 'Приветствие', ai: 'Incorrect', correct: 'Correct',
      excerpt: 'Оператор поздоровался и представился, но не спросил «удобно ли говорить».',
      reason: 'Приветствие засчитывается при наличии приветствия + имени + компании. Вопрос об удобстве не обязателен.',
      use_count: 7, by: 'Жанна', date: '28.06.2026' },
    { id: 2, direction: 'Основа', criterion: 'Персонализация', ai: 'Incorrect', correct: 'Correct',
      excerpt: 'Оператор обратился к клиенту по имени один раз в начале разговора.',
      reason: 'Достаточно одного обращения по имени за разговор — повторять не обязательно.',
      use_count: 4, by: 'Жанна', date: '27.06.2026' },
    { id: 3, direction: 'Яндекс Регистрация', criterion: 'КО_Достоверность информации', ai: 'Correct', correct: 'Incorrect',
      excerpt: 'Оператор сказал «комиссия 0%», хотя по направлению комиссия 17%.',
      reason: 'Неверная информация о комиссии — критическая ошибка достоверности. Всегда Incorrect.',
      use_count: 2, by: 'Алмаз', date: '26.06.2026' },
    { id: 4, direction: 'Поток', criterion: 'Отработка возражений', ai: 'N/A', correct: 'Correct',
      excerpt: 'Клиент сказал «подумаю», оператор привёл аргумент про бонус за 500 заказов.',
      reason: '«Подумаю» — это возражение; ответ оператора с выгодой засчитывается как отработка.',
      use_count: 3, by: 'Жанна', date: '25.06.2026' },
];

export default function AdjudicationsRag(props) {
    const { apiBaseUrl, withAccessTokenHeader } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [q, setQ] = useState('');
    const [dir, setDir] = useState('all');
    const [data, setData] = useState(null);   // null = загрузка
    const [demo, setDemo] = useState(false);

    useEffect(() => {
        if (!apiBaseUrl) { setData(MOCK); setDemo(true); return; }
        let live = true;
        axios.get(`${apiBaseUrl}/api/ai-qa/adjudications`, { headers: headers() })
            .then((r) => { if (live) { setData(r.data.items || []); setDemo(false); } })
            .catch(() => { if (live) { setData(MOCK); setDemo(true); } });
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
            {/* Поиск + фильтр направления */}
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

            <div className="px-1 text-[12px] text-slate-400">
                {data === null ? 'Загрузка…' : `${items.length} разбор(ов) · подтягиваются в оценки по смыслу`}
                {demo && <span className="ml-1 text-amber-600">· демо</span>}
            </div>

            {data === null ? (
                <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-12 text-slate-400`}>
                    <Loader2 size={20} className="animate-spin" />Загрузка…
                </div>
            ) : items.length === 0 ? (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <Database size={26} className="text-slate-300" />
                    <p className="text-[13px] text-slate-400">Ничего не найдено</p>
                </div>
            ) : (
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
                                <div className="flex shrink-0 items-center gap-1">
                                    <IosBadge tone="blue"><Repeat size={11} />{m.use_count}</IosBadge>
                                    <button className={iosBtnGhost + ' !px-2'} title="Изменить"><Pencil size={14} /></button>
                                    <button className={iosBtnGhost + ' !px-2 hover:!text-rose-600'} title="Удалить"><Trash2 size={14} /></button>
                                </div>
                            </div>
                            <p className="mt-2 flex gap-1.5 rounded-lg bg-slate-50 px-3 py-2 text-[12.5px] italic text-slate-600 ring-1 ring-slate-100">
                                <Quote size={13} className="mt-0.5 shrink-0 text-slate-300" />{m.excerpt}
                            </p>
                            <p className="mt-2 text-[13px] text-slate-700"><b className="text-slate-500">Правило:</b> {m.reason}</p>
                            <p className="mt-1.5 text-[11px] text-slate-400">{m.by} · {m.date}</p>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
