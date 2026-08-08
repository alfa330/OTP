import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    KeyRound, Loader2, Plus, ShieldCheck, Trash2, UserSearch,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary,
    IosBadge, IosModal, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

/* Выдача доступов.
 *
 * Ключевое отличие от исходной вики: там таблица правил не имела НИ ОДНОГО
 * CRUD-эндпоинта — единственным писателем был сид при старте, и отредактировать
 * правило через интерфейс было нельзя в принципе. Здесь правила заводятся руками.
 *
 * Субъект правила полиморфный: отдел / направление / группа / роль / роль вики /
 * конкретный человек. Это замена «должности» из оригинала — справочника
 * должностей у нас нет, а перечисленное есть и поддерживается в актуальном виде.
 */

const SUBJECT_KINDS = [
    { value: 'otp_role', label: 'Роль в системе' },
    { value: 'department', label: 'Отдел' },
    { value: 'group', label: 'Группа' },
    { value: 'direction', label: 'Направление' },
    { value: 'wiki_role', label: 'Роль в вики' },
    { value: 'user', label: 'Конкретный человек' },
];

const SUBJECT_KIND_LABEL = Object.fromEntries(SUBJECT_KINDS.map((k) => [k.value, k.label]));

const PERMISSIONS = [
    { key: 'can_read', label: 'Читать' },
    { key: 'can_create', label: 'Создавать' },
    { key: 'can_edit', label: 'Править' },
    { key: 'can_delete', label: 'Удалять', danger: true },
    { key: 'can_publish', label: 'Публиковать' },
    { key: 'can_approve', label: 'Согласовывать' },
];

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const emptyRule = (sectionId) => ({
    section_id: sectionId,
    subject_type: 'otp_role',
    subject_role: 'operator',
    subject_id: '',
    grant_subsections: true,
    can_read: true,
    can_create: false,
    can_edit: false,
    can_delete: false,
    can_publish: false,
    can_approve: false,
});

export default function WikiAccess({ base, headers, showToast, structure, reload }) {
    const sections = structure?.sections || [];
    const spaces = structure?.spaces || [];

    const [sectionId, setSectionId] = useState('');
    const [rules, setRules] = useState([]);
    const [catalog, setCatalog] = useState({});
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [draft, setDraft] = useState(null);
    const [probe, setProbe] = useState(null);

    useEffect(() => {
        if (!sectionId && sections.length) setSectionId(String(sections[0].id));
    }, [sections, sectionId]);

    useEffect(() => {
        axios.get(`${base}/access/subjects`, { headers })
            .then((r) => setCatalog(r.data || {}))
            .catch(() => setCatalog({}));
    }, [base, headers]);

    const loadRules = useCallback(() => {
        if (!sectionId) return;
        setLoading(true);
        axios.get(`${base}/access/section-rules`, { headers, params: { section_id: sectionId } })
            .then((r) => setRules(r.data?.items || []))
            .catch((e) => showToast?.(errText(e, 'Не удалось загрузить правила'), 'error'))
            .finally(() => setLoading(false));
    }, [base, headers, sectionId, showToast]);

    useEffect(() => { loadRules(); }, [loadRules]);

    const sectionOptions = useMemo(() => {
        const spaceName = new Map(spaces.map((s) => [s.id, s.name]));
        return sections.map((s) => ({
            value: String(s.id),
            label: `${spaceName.get(s.space_id) || '—'} · ${s.name}`,
        }));
    }, [sections, spaces]);

    const currentSection = sections.find((s) => String(s.id) === String(sectionId));

    const subjectOptions = useMemo(() => {
        const kind = draft?.subject_type;
        if (!kind || kind === 'otp_role' || kind === 'user') return [];
        return (catalog[kind] || []).map((item) => ({
            value: String(item.id), label: item.name,
        }));
    }, [catalog, draft?.subject_type]);

    const saveRule = () => {
        setBusy(true);
        axios.post(`${base}/access/section-rules`, {
            ...draft,
            section_id: Number(sectionId),
            subject_id: draft.subject_type === 'otp_role' ? null : Number(draft.subject_id) || null,
            subject_role: draft.subject_type === 'otp_role' ? draft.subject_role : null,
        }, { headers })
            .then(() => {
                showToast?.('Правило сохранено', 'success');
                setDraft(null);
                loadRules();
                reload?.();
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось сохранить правило'), 'error'))
            .finally(() => setBusy(false));
    };

    const removeRule = (rule) => {
        setBusy(true);
        axios.delete(`${base}/access/section-rules/${rule.id}`, { headers })
            .then(() => { showToast?.('Правило удалено', 'success'); loadRules(); reload?.(); })
            .catch((e) => showToast?.(errText(e, 'Не удалось удалить'), 'error'))
            .finally(() => setBusy(false));
    };

    const runProbe = (userId) => {
        if (!userId) return;
        setProbe({ loading: true });
        axios.get(`${base}/access/effective`, { headers, params: { user_id: userId } })
            .then((r) => setProbe({ data: r.data }))
            .catch((e) => setProbe({ error: errText(e, 'Не удалось проверить') }));
    };

    return (
        <div className="space-y-5">
            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Раздел</div>
                <div className={`${iosCard} p-4 space-y-3`}>
                    <CustomSelect
                        variant="ios"
                        value={sectionId}
                        onChange={setSectionId}
                        options={sectionOptions}
                        searchable
                        ariaLabel="Раздел для настройки доступа"
                    />
                    {currentSection?.visibility_scope === 'public' && (
                        <div className="flex items-start gap-2 rounded-xl bg-emerald-50 px-3 py-2.5 text-[12.5px] leading-relaxed text-emerald-800">
                            <ShieldCheck size={15} className="mt-0.5 shrink-0" />
                            <span>
                                Раздел публичный — его читают все сотрудники независимо от правил.
                                Правила ниже нужны только для прав на запись.
                            </span>
                        </div>
                    )}
                </div>
            </section>

            <section className="space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                    <div className={iosGroupLabel}>Правила</div>
                    <button
                        type="button"
                        className={iosBtnPrimary}
                        disabled={!sectionId}
                        onClick={() => setDraft(emptyRule(Number(sectionId)))}
                    >
                        <Plus size={15} /> Правило
                    </button>
                </div>

                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {loading && (
                        <div className="flex items-center justify-center gap-2 py-10 text-slate-400">
                            <Loader2 size={16} className="animate-spin" />
                            <span className="text-[13px]">Загружаем…</span>
                        </div>
                    )}
                    {!loading && rules.length === 0 && (
                        <div className="px-4 py-10 text-center">
                            <KeyRound size={20} className="mx-auto mb-2 text-slate-300" />
                            <div className="text-[13.5px] font-medium text-slate-700">Правил нет</div>
                            <p className="mx-auto mt-1 max-w-sm text-[12px] leading-relaxed text-slate-400">
                                {currentSection?.visibility_scope === 'public'
                                    ? 'Читать раздел могут все — это публичный раздел.'
                                    : 'Пока правил нет, раздел не видит никто, кроме администратора вики.'}
                            </p>
                        </div>
                    )}
                    {!loading && rules.map((rule) => (
                        <div key={rule.id} className="flex flex-wrap items-center gap-2 px-4 py-3">
                            <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-1.5">
                                    <IosBadge tone="blue">
                                        {SUBJECT_KIND_LABEL[rule.subject_type] || rule.subject_type}
                                    </IosBadge>
                                    <span className="truncate text-[13.5px] font-medium text-slate-900">
                                        {rule.subject_label || rule.subject_role || `#${rule.subject_id}`}
                                    </span>
                                </div>
                                <div className="mt-1 flex flex-wrap gap-1">
                                    {PERMISSIONS.filter((p) => rule[p.key]).map((p) => (
                                        <IosBadge key={p.key} tone={p.danger ? 'amber' : 'slate'}>
                                            {p.label}
                                        </IosBadge>
                                    ))}
                                    {rule.grant_subsections && (
                                        <IosBadge tone="slate">+ подразделы</IosBadge>
                                    )}
                                </div>
                            </div>
                            <button
                                type="button"
                                disabled={busy}
                                onClick={() => removeRule(rule)}
                                className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40"
                                aria-label="Удалить правило"
                            >
                                <Trash2 size={14} />
                            </button>
                        </div>
                    ))}
                </div>
            </section>

            {/* Проверка «почему человек это видит» — в оригинале такого нет,
                а при нескольких уровнях правил без неё не разобраться. */}
            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Проверить сотрудника</div>
                <div className={`${iosCard} p-4 space-y-3`}>
                    <div className="flex flex-wrap items-end gap-2">
                        <div className="min-w-[180px] flex-1">
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                ID сотрудника
                            </label>
                            <input
                                className={iosInput}
                                inputMode="numeric"
                                placeholder="Например: 42"
                                onKeyDown={(e) => { if (e.key === 'Enter') runProbe(e.currentTarget.value); }}
                                onBlur={(e) => { if (e.target.value) runProbe(e.target.value); }}
                            />
                        </div>
                        <UserSearch size={18} className="mb-2.5 text-slate-300" />
                    </div>

                    {probe?.loading && (
                        <div className="flex items-center gap-2 text-[13px] text-slate-400">
                            <Loader2 size={14} className="animate-spin" /> Считаем периметр…
                        </div>
                    )}
                    {probe?.error && (
                        <div className="text-[13px] text-rose-600">{probe.error}</div>
                    )}
                    {probe?.data && (
                        <div className="space-y-2">
                            <div className="flex flex-wrap gap-1.5">
                                <IosBadge tone="slate">роль: {probe.data.otp_role}</IosBadge>
                                <IosBadge tone={probe.data.access_mode === 'manual' ? 'amber' : 'slate'}>
                                    {probe.data.access_mode === 'manual' ? 'ручная выдача' : 'авто'}
                                </IosBadge>
                                <IosBadge tone="blue">
                                    разделов: {probe.data.sections?.length || 0}
                                </IosBadge>
                            </div>
                            <div className="max-h-64 overflow-y-auto rounded-xl bg-slate-50">
                                {(probe.data.sections || []).length === 0 && (
                                    <div className="px-3 py-6 text-center text-[12.5px] text-slate-400">
                                        Сотрудник не видит ни одного раздела
                                    </div>
                                )}
                                {(probe.data.sections || []).map((s) => (
                                    <div key={s.id} className="flex items-center justify-between gap-3 border-b border-slate-100 px-3 py-2 last:border-0">
                                        <span className="truncate text-[13px] text-slate-800">{s.name}</span>
                                        <span className="shrink-0 text-[11.5px] text-slate-400">{s.why}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </section>

            {/* ── Форма правила ── */}
            <IosModal
                open={!!draft}
                onClose={() => setDraft(null)}
                title="Правило доступа"
                subtitle={currentSection ? `Раздел «${currentSection.name}»` : ''}
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => setDraft(null)}>
                            Отмена
                        </button>
                        <button type="button" className={iosBtnPrimary} disabled={busy} onClick={saveRule}>
                            {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                        </button>
                    </>
                )}
            >
                {draft && (
                    <div className="space-y-3.5">
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Кому</label>
                            <CustomSelect
                                variant="ios"
                                value={draft.subject_type}
                                onChange={(v) => setDraft({ ...draft, subject_type: v, subject_id: '' })}
                                options={SUBJECT_KINDS}
                                ariaLabel="Тип субъекта"
                            />
                        </div>

                        {draft.subject_type === 'otp_role' && (
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Роль</label>
                                <CustomSelect
                                    variant="ios"
                                    value={draft.subject_role}
                                    onChange={(v) => setDraft({ ...draft, subject_role: v })}
                                    options={(catalog.otp_role || []).map((r) => ({
                                        value: String(r.id), label: r.name,
                                    }))}
                                    ariaLabel="Роль в системе"
                                />
                                <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-slate-400">
                                    Правило действует и на роли выше: выдав доступ оператору,
                                    вы автоматически открываете его супервайзеру и администратору.
                                </p>
                            </div>
                        )}

                        {draft.subject_type === 'user' && (
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                    ID сотрудника
                                </label>
                                <input
                                    className={iosInput}
                                    inputMode="numeric"
                                    value={draft.subject_id}
                                    onChange={(e) => setDraft({ ...draft, subject_id: e.target.value })}
                                    placeholder="Например: 42"
                                />
                            </div>
                        )}

                        {!['otp_role', 'user'].includes(draft.subject_type) && (
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                    {SUBJECT_KIND_LABEL[draft.subject_type]}
                                </label>
                                <CustomSelect
                                    variant="ios"
                                    value={draft.subject_id}
                                    onChange={(v) => setDraft({ ...draft, subject_id: v })}
                                    options={subjectOptions}
                                    searchable
                                    ariaLabel="Субъект правила"
                                />
                            </div>
                        )}

                        <div className="space-y-1.5">
                            <div className={iosGroupLabel}>Что разрешено</div>
                            <div className={`${iosCard} divide-y divide-slate-100`}>
                                {PERMISSIONS.map((p) => (
                                    <div key={p.key} className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                                        <span className={`text-[13.5px] ${p.danger ? 'text-amber-700' : 'text-slate-700'}`}>
                                            {p.label}
                                        </span>
                                        <IosToggle
                                            checked={!!draft[p.key]}
                                            disabled={p.key === 'can_read' && PERMISSIONS.some(
                                                (x) => x.key !== 'can_read' && draft[x.key])}
                                            onChange={(v) => setDraft({ ...draft, [p.key]: v, ...(v ? { can_read: true } : {}) })}
                                        />
                                    </div>
                                ))}
                            </div>
                            <p className="px-1 text-[11.5px] text-slate-400">
                                Право на запись невозможно без чтения — оно включается само.
                            </p>
                        </div>

                        <div className={`${iosCard} flex items-start justify-between gap-3 p-3.5`}>
                            <div className="min-w-0">
                                <div className="text-[13.5px] font-medium text-slate-900">
                                    Распространить на подразделы
                                </div>
                                <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                                    Правило подействует и на все вложенные разделы, включая созданные позже.
                                </p>
                            </div>
                            <IosToggle
                                checked={!!draft.grant_subsections}
                                onChange={(v) => setDraft({ ...draft, grant_subsections: v })}
                            />
                        </div>
                    </div>
                )}
            </IosModal>
        </div>
    );
}
