import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Search, Database, Check, X, Minus, ArrowRight, Quote, Repeat, Loader2,
    Pencil, Trash2, Save,
} from 'lucide-react';
import { APPLE_FONT, iosCard, iosInput, iosBtnGhost, iosBtnPrimary, IosBadge } from '../ui/ios';

/* База разборов (RAG) — реальные данные с GET /api/ai-qa/adjudications.
 * canManage (супер-админ): правка и удаление разборов — PUT/DELETE
 * /api/ai-qa/adjudications/<id>; бэкенд дублирует проверку роли. */

const V = { Correct: { t: 'green', l: 'Верно', I: Check }, Incorrect: { t: 'red', l: 'Неверно', I: X }, 'N/A': { t: 'slate', l: 'N/A', I: Minus } };
const Verdict = ({ v }) => { const m = V[v] || V['N/A']; return <IosBadge tone={m.t}><m.I size={11} />{m.l}</IosBadge>; };

const fieldCls = `${iosInput} px-3 py-2 text-[12.5px]`;

/* Инлайн-форма правки: вердикт + правило + границы. Embedding пересчитается на бэкенде. */
function EditForm({ item, onCancel, onSaved, onBusyChange, apiBaseUrl, headers, showToast }) {
    const [draft, setDraft] = useState({
        correct_verdict: item.correct, reason: item.reason || '',
        situation: item.situation || '', not_covered: item.not_covered || '',
    });
    const [saving, setSaving] = useState(false);
    const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));

    const save = () => {
        if (saving) return;
        const payload = {
            ...draft,
            reason: draft.reason.trim(),
            situation: draft.situation.trim() || null,
            not_covered: draft.not_covered.trim() || null,
        };
        if (!payload.reason) { showToast?.('Правило не может быть пустым', 'error'); return; }
        setSaving(true);
        onBusyChange?.(true);
        axios.put(`${apiBaseUrl}/api/ai-qa/adjudications/${item.id}`, payload, { headers: headers() })
            .then(() => { showToast?.('Разбор обновлён', 'success'); onSaved(payload); })
            .catch((e) => showToast?.(e?.response?.data?.error || 'Не удалось сохранить', 'error'))
            .finally(() => { setSaving(false); onBusyChange?.(false); });
    };

    return (
        <div className="mt-3 space-y-2 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200/70">
            <div>
                <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Правильный вердикт</div>
                <div className="inline-flex rounded-xl bg-slate-100 p-0.5">
                    {Object.keys(V).map((v) => (
                        <button key={v} type="button" disabled={saving}
                            onClick={() => setDraft((d) => ({ ...d, correct_verdict: v }))}
                            className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-[12px] font-semibold transition ${
                                draft.correct_verdict === v ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}>
                            {V[v].l}
                        </button>
                    ))}
                </div>
            </div>
            <div>
                <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Правило (почему так правильно)</div>
                <textarea rows={2} value={draft.reason} onChange={set('reason')} disabled={saving}
                    className={`${fieldCls} w-full resize-y`} />
            </div>
            <div>
                <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Ситуация (когда применять)</div>
                <input value={draft.situation} onChange={set('situation')} disabled={saving}
                    className={`${fieldCls} w-full`} placeholder="пусто — без ограничения по ситуации" />
            </div>
            <div>
                <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Чего правило НЕ оправдывает</div>
                <input value={draft.not_covered} onChange={set('not_covered')} disabled={saving}
                    className={`${fieldCls} w-full`} placeholder="границы: какие нарушения не прощаются" />
            </div>
            <div className="flex items-center justify-end gap-2 pt-1">
                <button type="button" onClick={onCancel} disabled={saving} className={iosBtnGhost}>Отмена</button>
                <button type="button" onClick={save} disabled={saving} className={iosBtnPrimary}>
                    {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}Сохранить
                </button>
            </div>
        </div>
    );
}

export default function AdjudicationsRag(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast, canManage, onInteractionChange } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [q, setQ] = useState('');
    const [dir, setDir] = useState('all');
    const [data, setData] = useState(null);   // null = загрузка
    const [editId, setEditId] = useState(null);
    const [busy, setBusy] = useState(null); // { id, action: 'save' | 'delete' }

    useEffect(() => {
        if (!apiBaseUrl) { setData([]); return; }
        let live = true;
        axios.get(`${apiBaseUrl}/api/ai-qa/adjudications`, { headers: headers() })
            .then((r) => { if (live) setData(r.data.items || []); })
            .catch(() => { if (live) setData([]); });
        return () => { live = false; };
        // eslint-disable-next-line
    }, [apiBaseUrl]);

    useEffect(() => {
        onInteractionChange?.({ editing: editId !== null, busy: Boolean(busy) });
    }, [editId, busy, onInteractionChange]);

    useEffect(() => () => {
        onInteractionChange?.({ editing: false, busy: false });
    }, [onInteractionChange]);

    useEffect(() => {
        if (dir !== 'all' && data && !data.some((item) => item.direction === dir)) setDir('all');
    }, [data, dir]);

    const dirs = useMemo(() => ['all', ...Array.from(new Set((data || []).map((m) => m.direction)))], [data]);
    const items = useMemo(() => (data || []).filter((m) =>
        (dir === 'all' || m.direction === dir) &&
        (q.trim() === '' || `${m.criterion} ${m.excerpt} ${m.reason}`.toLowerCase().includes(q.toLowerCase()))
    ), [q, dir, data]);

    const remove = (m) => {
        if (busy || editId !== null) return;
        if (!window.confirm(`Удалить разбор «${m.criterion}»? Правило перестанет подтягиваться в оценки.`)) return;
        setBusy({ id: m.id, action: 'delete' });
        axios.delete(`${apiBaseUrl}/api/ai-qa/adjudications/${m.id}`, { headers: headers() })
            .then(() => { showToast?.('Разбор удалён', 'success'); setData((d) => (d || []).filter((x) => x.id !== m.id)); })
            .catch((e) => showToast?.(e?.response?.data?.error || 'Не удалось удалить', 'error'))
            .finally(() => setBusy(null));
    };

    const applyEdit = (id, draft) => {
        setData((d) => (d || []).map((x) => (x.id === id ? {
            ...x, correct: draft.correct_verdict, reason: draft.reason,
            situation: draft.situation || null, not_covered: draft.not_covered || null,
        } : x)));
        setEditId(null);
    };

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative flex-1 min-w-[220px]">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={q} onChange={(e) => setQ(e.target.value)} disabled={editId !== null || Boolean(busy)}
                        placeholder="Поиск по критерию, ситуации, правилу…"
                        className="w-full rounded-xl bg-slate-100 py-2.5 pl-9 pr-3 text-[13.5px] text-slate-800 placeholder-slate-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/60" />
                </div>
                <div className="flex flex-wrap gap-1.5">
                    {dirs.map((d) => (
                        <button key={d} type="button" onClick={() => setDir(d)}
                            disabled={editId !== null || Boolean(busy)}
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
                                    <div className="flex shrink-0 items-center gap-1.5">
                                        <IosBadge tone="blue"><Repeat size={11} />{m.use_count}</IosBadge>
                                        {canManage && (
                                            <>
                                                <button type="button" title="Редактировать разбор"
                                                    disabled={Boolean(busy) || (editId !== null && editId !== m.id)}
                                                    onClick={() => setEditId(editId === m.id ? null : m.id)}
                                                    className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50">
                                                    <Pencil size={14} />
                                                </button>
                                                <button type="button" title="Удалить разбор" onClick={() => remove(m)}
                                                    disabled={Boolean(busy) || editId !== null}
                                                    className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50">
                                                    {busy?.action === 'delete' && busy.id === m.id
                                                        ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                                                </button>
                                            </>
                                        )}
                                    </div>
                                </div>
                                {editId === m.id ? (
                                    <EditForm item={m} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                                              onCancel={() => setEditId(null)} onSaved={(draft) => applyEdit(m.id, draft)}
                                              onBusyChange={(isBusy) => setBusy(isBusy ? { id: m.id, action: 'save' } : null)} />
                                ) : (
                                    <>
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
                                    </>
                                )}
                            </div>
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}
