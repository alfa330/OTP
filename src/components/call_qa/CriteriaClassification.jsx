import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Sparkles, Server, User2, ShieldAlert, Save, Info, Loader2 } from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnPrimary, IosBadge } from '../ui/ios';

/* Классификация критериев по источнику оценки.
 * Тянется с GET /api/ai-qa/criteria-config?direction_id=, сохраняется POST. Откат на демо. */

const SOURCES = [
    { key: 'transcript', label: 'ИИ',     Icon: Sparkles, tone: 'blue' },
    { key: 'system_api', label: 'ПО-API', Icon: Server,   tone: 'amber' },
    { key: 'manual',     label: 'Ручная', Icon: User2,    tone: 'slate' },
];

const DIRECTIONS = [{ id: 73, name: 'Основа' }, { id: 72, name: 'Яндекс Регистрация' }, { id: 74, name: 'Поток' }];

const MOCK = [
    { idx: 0, name: 'Приветствие', is_critical: false, source: 'transcript' },
    { idx: 2, name: 'Персонализация', is_critical: false, source: 'transcript' },
    { idx: 3, name: 'Идентификация клиента', is_critical: false, source: 'transcript' },
    { idx: 6, name: 'Отработка возражений', is_critical: false, source: 'transcript' },
    { idx: 12, name: 'Корректность оформления регистрации', is_critical: false, source: 'system_api' },
    { idx: 15, name: 'КО_Достоверность информации', is_critical: true, source: 'transcript' },
    { idx: 19, name: 'КО_Внесение информации в ПО', is_critical: true, source: 'system_api' },
    { idx: 20, name: 'Сделка состоялась', is_critical: false, source: 'system_api' },
    { idx: 21, name: 'Нет критических ошибок', is_critical: true, source: 'transcript' },
];

function SourcePicker({ value, onChange }) {
    return (
        <div className="inline-flex shrink-0 rounded-xl bg-slate-100 p-0.5">
            {SOURCES.map((s) => {
                const active = value === s.key;
                return (
                    <button key={s.key} onClick={() => onChange(s.key)}
                        className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-[12px] font-semibold transition ${
                            active ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}>
                        <s.Icon size={12} />{s.label}
                    </button>
                );
            })}
        </div>
    );
}

export default function CriteriaClassification(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});

    const [dir, setDir] = useState(73);
    const [rows, setRows] = useState(null);   // null = загрузка
    const [demo, setDemo] = useState(false);
    const [dirty, setDirty] = useState(false);
    const [saving, setSaving] = useState(false);

    const loadDir = (id) => {
        setDir(id); setRows(null); setDirty(false);
        if (!apiBaseUrl) { setRows(MOCK.map((r) => ({ ...r }))); setDemo(true); return; }
        axios.get(`${apiBaseUrl}/api/ai-qa/criteria-config`, { params: { direction_id: id }, headers: headers() })
            .then((r) => { setRows(r.data.criteria || []); setDemo(false); })
            .catch(() => { setRows(MOCK.map((x) => ({ ...x }))); setDemo(true); });
    };

    useEffect(() => { loadDir(73); /* eslint-disable-next-line */ }, [apiBaseUrl]);

    const counts = useMemo(() => {
        const c = { transcript: 0, system_api: 0, manual: 0 };
        (rows || []).forEach((r) => { c[r.source] = (c[r.source] || 0) + 1; });
        return c;
    }, [rows]);

    const setSource = (idx, source) => {
        setRows((rs) => rs.map((r) => (r.idx === idx ? { ...r, source } : r)));
        setDirty(true);
    };

    const save = () => {
        if (!apiBaseUrl) { showToast?.('Классификация сохранена', 'success'); setDirty(false); return; }
        setSaving(true);
        const items = rows.map((r) => ({ criterion_idx: r.idx, eval_source: r.source }));
        axios.post(`${apiBaseUrl}/api/ai-qa/criteria-config`, { direction_id: dir, items }, { headers: headers() })
            .then(() => { showToast?.('Классификация сохранена', 'success'); setDirty(false); })
            .catch(() => showToast?.('Не удалось сохранить', 'error'))
            .finally(() => setSaving(false));
    };

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                {DIRECTIONS.map((d) => (
                    <button key={d.id} onClick={() => loadDir(d.id)}
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

            <p className="flex items-start gap-1.5 px-1 text-[12px] text-slate-400">
                <Info size={14} className="mt-0.5 shrink-0" />
                «ПО-API» — критерий проверяется не по разговору, а по данным в системе. {demo && <span className="text-amber-600">Демо-данные.</span>}
            </p>

            {rows === null ? (
                <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-12 text-slate-400`}>
                    <Loader2 size={20} className="animate-spin" />Загрузка…
                </div>
            ) : (
                <div className="space-y-2">
                    {rows.map((r) => (
                        <div key={r.idx} className={`${iosCard} flex items-center justify-between gap-3 p-3.5`}>
                            <div className="flex min-w-0 items-center gap-1.5">
                                {r.is_critical && <ShieldAlert size={14} className="shrink-0 text-rose-500" title="Критический" />}
                                <span className="truncate text-[13.5px] font-medium text-slate-800">{r.name}</span>
                            </div>
                            <SourcePicker value={r.source} onChange={(s) => setSource(r.idx, s)} />
                        </div>
                    ))}
                </div>
            )}

            <div className="sticky bottom-0 flex items-center justify-between rounded-2xl bg-white/85 px-3 py-2.5 ring-1 ring-slate-200/70 backdrop-blur-xl">
                <span className="text-[12.5px] text-slate-500">{dirty ? 'Есть несохранённые изменения' : 'Классификация направления'}</span>
                <button disabled={!dirty || saving} onClick={save} className={iosBtnPrimary}>
                    {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}Сохранить
                </button>
            </div>
        </div>
    );
}
