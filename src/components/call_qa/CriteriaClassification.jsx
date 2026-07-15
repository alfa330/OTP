import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Sparkles, Server, User2, ShieldAlert, Save, Info, Loader2, AlertCircle, RefreshCw } from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnPrimary, iosBtnSecondary, IosBadge } from '../ui/ios';

/* Классификация критериев по источнику. Реальные данные: GET/POST /api/ai-qa/criteria-config. */

const SOURCES = [
    { key: 'transcript', label: 'По разговору', Icon: Sparkles, tone: 'blue' },
    { key: 'system_api', label: 'По данным',     Icon: Server,   tone: 'amber' },
    { key: 'manual',     label: 'Ручная', Icon: User2,    tone: 'slate' },
];

const FALLBACK_DIRECTIONS = [{ id: 73, name: 'Основа' }, { id: 72, name: 'Яндекс Регистрация' }, { id: 74, name: 'Поток' }];

function SourcePicker({ value, onChange, disabled = false }) {
    return (
        <div className="grid w-full grid-cols-3 rounded-xl bg-slate-100 p-0.5 sm:inline-flex sm:w-auto sm:shrink-0"
             role="group" aria-label="Источник проверки критерия">
            {SOURCES.map((s) => {
                const active = value === s.key;
                return (
                    <button key={s.key} type="button" onClick={() => onChange(s.key)} aria-pressed={active} disabled={disabled}
                        className={`flex min-h-9 items-center justify-center gap-1 rounded-lg px-2 py-1.5 text-[11.5px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                            active ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-400 hover:text-slate-600'} disabled:cursor-not-allowed disabled:opacity-50`}>
                        <s.Icon size={12} />{s.label}
                    </button>
                );
            })}
        </div>
    );
}

export default function CriteriaClassification(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast, directions, onInteractionChange } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const availableDirections = useMemo(() => {
        const liveNames = new Map((directions || []).map((item) => [String(item.id), item.name]));
        return FALLBACK_DIRECTIONS.map((item) => ({
            ...item, name: liveNames.get(String(item.id)) || item.name,
        }));
    }, [directions]);

    const [dir, setDir] = useState(FALLBACK_DIRECTIONS[0].id);
    const [rows, setRows] = useState(null);   // null = загрузка
    const [err, setErr] = useState(null);
    const [dirty, setDirty] = useState(false);
    const [saving, setSaving] = useState(false);
    const requestRef = useRef({ id: 0, controller: null });

    const loadDir = (id, { force = false } = {}) => {
        if (saving) {
            showToast?.('Дождитесь завершения сохранения', 'error');
            return;
        }
        if (!force && id !== dir && dirty &&
            !window.confirm('Сменить направление? Несохранённые изменения будут потеряны.')) return;
        requestRef.current.controller?.abort();
        const controller = new AbortController();
        const requestId = requestRef.current.id + 1;
        requestRef.current = { id: requestId, controller };
        setDir(id); setRows(null); setErr(null); setDirty(false);
        if (!apiBaseUrl) {
            setRows([]); setErr('Сервис критериев не настроен');
            return;
        }
        axios.get(`${apiBaseUrl}/api/ai-qa/criteria-config`, {
            params: { direction_id: id }, headers: headers(), signal: controller.signal,
        })
            .then((r) => {
                if (requestId === requestRef.current.id) setRows(r.data.criteria || []);
            })
            .catch((error) => {
                if (!axios.isCancel(error) && requestId === requestRef.current.id) {
                    setRows([]);
                    setErr(error?.response?.data?.error || 'Не удалось загрузить критерии');
                }
            });
    };

    useEffect(() => {
        loadDir(FALLBACK_DIRECTIONS[0].id, { force: true });
        return () => requestRef.current.controller?.abort();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl]);

    useEffect(() => {
        onInteractionChange?.({ editing: dirty, busy: saving });
    }, [dirty, saving, onInteractionChange]);

    useEffect(() => () => onInteractionChange?.({ editing: false, busy: false }), [onInteractionChange]);

    const counts = useMemo(() => {
        const c = { transcript: 0, system_api: 0, manual: 0 };
        (rows || []).forEach((r) => { c[r.source] = (c[r.source] || 0) + 1; });
        return c;
    }, [rows]);

    const setSource = (idx, source) => {
        if (saving) return;
        setRows((rs) => rs.map((r) => (r.idx === idx ? { ...r, source } : r)));
        setDirty(true);
    };

    const save = () => {
        if (!apiBaseUrl) { showToast?.('Бэкенд недоступен', 'error'); return; }
        if (!rows?.length || saving) return;
        setSaving(true);
        const items = rows.map((r) => ({ criterion_idx: r.idx, eval_source: r.source }));
        axios.post(`${apiBaseUrl}/api/ai-qa/criteria-config`, { direction_id: dir, items }, { headers: headers() })
            .then(() => { showToast?.('Классификация сохранена', 'success'); setDirty(false); })
            .catch(() => showToast?.('Не удалось сохранить', 'error'))
            .finally(() => setSaving(false));
    };

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Направление продаж">
                {availableDirections.map((d) => (
                    <button key={d.id} type="button" onClick={() => loadDir(d.id)} disabled={saving}
                        aria-pressed={dir === d.id}
                        className={`rounded-xl px-3.5 py-2 text-[13px] font-semibold transition ${
                            dir === d.id ? 'bg-blue-600 text-white shadow-sm' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                        {d.name}
                    </button>
                ))}
                <div className="ml-auto flex items-center gap-1.5">
                    {SOURCES.map((s) => (
                        <IosBadge key={s.key} tone={s.tone}><s.Icon size={11} />{counts[s.key]}</IosBadge>
                    ))}
                </div>
            </div>

            <p className="flex items-start gap-1.5 px-1 text-[12px] text-slate-500">
                <Info size={14} className="mt-0.5 shrink-0" />
                «По данным» — критерий проверяется не по разговору, а по информации в рабочей системе.
            </p>

            {rows === null ? (
                <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-12 text-slate-500`} role="status">
                    <Loader2 size={20} className="animate-spin" aria-hidden="true" />Загрузка критериев…
                </div>
            ) : err ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-12 text-center`} role="alert">
                    <AlertCircle size={24} className="text-rose-500" />
                    <p className="text-[13px] font-medium text-slate-700">{err}</p>
                    <button type="button" onClick={() => loadDir(dir, { force: true })} className={iosBtnSecondary}>
                        <RefreshCw size={14} />Повторить
                    </button>
                </div>
            ) : rows.length === 0 ? (
                <div className={`${iosCard} px-6 py-12 text-center text-[13px] text-slate-500`}>
                    Для этого направления критерии пока не настроены.
                </div>
            ) : (
                <div className="space-y-2">
                    {rows.map((r) => (
                        <div key={r.idx} className={`${iosCard} flex flex-col items-stretch justify-between gap-3 p-3.5 sm:flex-row sm:items-center`}>
                            <div className="flex min-w-0 items-center gap-1.5">
                                {r.is_critical && <ShieldAlert size={14} className="shrink-0 text-rose-500" title="Критический" />}
                                <span className="text-[13.5px] font-medium leading-snug text-slate-800">{r.name}</span>
                            </div>
                            <SourcePicker value={r.source} onChange={(s) => setSource(r.idx, s)} disabled={saving} />
                        </div>
                    ))}
                </div>
            )}

            <div className="sticky bottom-0 flex flex-col gap-2 rounded-2xl bg-white/95 px-3 py-2.5 ring-1 ring-slate-200/70 backdrop-blur-xl sm:flex-row sm:items-center sm:justify-between" aria-live="polite">
                <span className="text-[12.5px] text-slate-500">{dirty ? 'Есть несохранённые изменения' : 'Классификация направления'}</span>
                <button type="button" disabled={!dirty || saving || !rows?.length} onClick={save} className={iosBtnPrimary}>
                    {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                    {saving ? 'Сохраняю…' : 'Сохранить'}
                </button>
            </div>
        </div>
    );
}
