import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import FaIcon from '../common/FaIcon';
import { normalizeRole } from '../../utils/roles';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosModal,
} from '../ui/ios';

// Модель → тон бейджа. Источник истины моделей — каталог с бэка (calculation_models).
const MODEL_TONE = { operator: 'blue', chat_manager: 'green' };
const FALLBACK_MODELS = [
    { code: 'operator', name: 'Операторская модель' },
    { code: 'chat_manager', name: 'Модель чат-менеджера' },
];
const EMPTY_FORM = { name: '', department_id: '', direction_id: '', calculation_model_code: 'operator' };

const GroupsView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader }) => {
    const [groups, setGroups] = useState([]);
    const [directions, setDirections] = useState([]);
    const [calcModels, setCalcModels] = useState([]);
    const [departments, setDepartments] = useState([]);
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [showArchived, setShowArchived] = useState(false);
    const [search, setSearch] = useState('');

    // create modal
    const [createOpen, setCreateOpen] = useState(false);
    const [form, setForm] = useState({ ...EMPTY_FORM });
    const [suggestions, setSuggestions] = useState([]);
    const [saving, setSaving] = useState(false);

    // members modal
    const [membersGroup, setMembersGroup] = useState(null);
    const [members, setMembers] = useState({ operators: [], supervisors: [] });
    const [membersLoading, setMembersLoading] = useState(false);
    const [addOpId, setAddOpId] = useState('');
    const [addSvId, setAddSvId] = useState('');
    const [effDate, setEffDate] = useState('');
    const [memberBusy, setMemberBusy] = useState(false);

    const showToastRef = useRef(showToast);
    showToastRef.current = showToast;

    const authHeaders = useCallback(
        (extra = {}) => withAccessTokenHeader({ 'X-User-Id': String(user.id), ...extra }),
        [withAccessTokenHeader, user?.id]
    );

    const api = useCallback(async (path, opts = {}) => {
        const hasBody = opts.body !== undefined;
        const resp = await fetch(`${apiBaseUrl}${path}`, {
            method: opts.method || 'GET',
            credentials: 'include',
            headers: authHeaders(hasBody ? { 'Content-Type': 'application/json' } : {}),
            body: hasBody ? JSON.stringify(opts.body) : undefined,
        });
        const data = await resp.json().catch(() => ({}));
        return { ok: resp.ok, data };
    }, [apiBaseUrl, authHeaders]);

    const fetchGroups = useCallback(async () => {
        setLoading(true);
        try {
            const { ok, data } = await api('/api/groups?include_archived=true');
            if (ok && Array.isArray(data.groups)) setGroups(data.groups);
            else showToastRef.current?.(data.error || 'Не удалось загрузить группы', 'error');
        } catch {
            showToastRef.current?.('Ошибка сети при загрузке групп', 'error');
        } finally {
            setLoading(false);
        }
    }, [api]);

    const fetchAux = useCallback(async () => {
        try {
            const [d, u, dep] = await Promise.all([
                api('/api/admin/directions'),
                api('/api/admin/users'),
                api('/api/admin/departments'),
            ]);
            if (d.ok) {
                setDirections(d.data.directions || []);
                setCalcModels((d.data.calculation_models || []).length ? d.data.calculation_models : FALLBACK_MODELS);
            } else {
                setCalcModels(FALLBACK_MODELS);
            }
            if (u.ok) setUsers(u.data.users || u.data.operators || (Array.isArray(u.data) ? u.data : []));
            if (dep.ok) setDepartments(dep.data.departments || []);
        } catch {
            setCalcModels(FALLBACK_MODELS);
        }
    }, [api]);

    useEffect(() => { fetchGroups(); fetchAux(); }, [fetchGroups, fetchAux]);

    const dirModelOf = (dir) => String(dir?.calculationModelCode || dir?.calculation_model_code || 'operator');

    const operatorsList = useMemo(
        () => (users || []).filter((u) => ['operator', 'trainee'].includes(normalizeRole(u.role))),
        [users]
    );
    const supervisorsList = useMemo(
        () => (users || []).filter((u) => ['sv', 'supervisor'].includes(normalizeRole(u.role))),
        [users]
    );
    const modelName = (code) => (calcModels.find((m) => m.code === code) || {}).name || code;

    const visibleGroups = useMemo(() => {
        const q = search.trim().toLowerCase();
        return (groups || [])
            .filter((g) => showArchived || g.status !== 'archived')
            .filter((g) => !q || (g.name || '').toLowerCase().includes(q));
    }, [groups, showArchived, search]);

    /* ─── create ─── */
    const openCreate = () => { setForm({ ...EMPTY_FORM }); setSuggestions([]); setCreateOpen(true); };
    const closeCreate = () => { setCreateOpen(false); setForm({ ...EMPTY_FORM }); setSuggestions([]); };

    const submitCreate = async (force = false) => {
        if (!form.name.trim()) { showToastRef.current?.('Укажите название группы', 'error'); return; }
        setSaving(true);
        try {
            const body = {
                name: form.name.trim(),
                calculation_model_code: form.calculation_model_code,
                direction_id: form.direction_id ? Number(form.direction_id) : null,
                department_id: form.department_id ? Number(form.department_id) : null,
            };
            if (force) body.force = true;
            const { ok, data } = await api('/api/admin/groups', { method: 'POST', body });
            if (data.status === 'suggestions') {
                setSuggestions(data.suggestions || []);
                showToastRef.current?.('Есть похожие архивные группы — можно переиспользовать', 'info');
                return;
            }
            if (ok && data.group) {
                showToastRef.current?.('Группа создана', 'success');
                closeCreate();
                fetchGroups();
            } else {
                showToastRef.current?.(data.error || 'Не удалось создать группу', 'error');
            }
        } catch {
            showToastRef.current?.('Ошибка сети при создании группы', 'error');
        } finally {
            setSaving(false);
        }
    };

    const reuseSuggestion = async (groupId) => {
        setSaving(true);
        try {
            const { ok, data } = await api('/api/admin/groups', { method: 'POST', body: { reuse_group_id: groupId } });
            if (ok && data.group) {
                showToastRef.current?.('Архивная группа возвращена в работу', 'success');
                closeCreate();
                fetchGroups();
            } else {
                showToastRef.current?.(data.error || 'Не удалось переиспользовать группу', 'error');
            }
        } finally {
            setSaving(false);
        }
    };

    /* ─── archive / reuse ─── */
    const setGroupArchived = async (group, archive) => {
        const path = `/api/admin/groups/${group.id}/${archive ? 'archive' : 'reuse'}`;
        const { ok, data } = await api(path, { method: 'POST', body: {} });
        if (ok && data.group) {
            setGroups((prev) => prev.map((g) => (g.id === group.id ? data.group : g)));
            showToastRef.current?.(archive ? 'Группа архивирована' : 'Группа возвращена', 'success');
        } else {
            showToastRef.current?.(data.error || 'Не удалось изменить статус группы', 'error');
        }
    };

    /* ─── members ─── */
    const openMembers = async (group) => {
        setMembersGroup(group);
        setAddOpId(''); setAddSvId(''); setEffDate('');
        setMembersLoading(true);
        try {
            const { ok, data } = await api(`/api/admin/groups/${group.id}/members`);
            if (ok) setMembers({ operators: data.operators || [], supervisors: data.supervisors || [] });
            else showToastRef.current?.(data.error || 'Не удалось загрузить состав', 'error');
        } finally {
            setMembersLoading(false);
        }
    };
    const closeMembers = () => { setMembersGroup(null); setMembers({ operators: [], supervisors: [] }); };

    const refreshMembers = async (groupId) => {
        const { ok, data } = await api(`/api/admin/groups/${groupId}/members`);
        if (ok) setMembers({ operators: data.operators || [], supervisors: data.supervisors || [] });
        fetchGroups(); // обновить счётчики/СВ в списке
    };

    const mutateMember = async (kind, payload) => {
        if (!membersGroup) return;
        setMemberBusy(true);
        try {
            const path = `/api/admin/groups/${membersGroup.id}/${kind}`;
            const { ok, data } = await api(path, { method: 'POST', body: payload });
            if (ok) await refreshMembers(membersGroup.id);
            else showToastRef.current?.(data.error || 'Не удалось обновить состав', 'error');
        } catch {
            showToastRef.current?.('Ошибка сети', 'error');
        } finally {
            setMemberBusy(false);
        }
    };

    const addOperator = () => {
        if (!addOpId) return;
        mutateMember('operators', { operator_id: Number(addOpId), start_date: effDate || null });
        setAddOpId('');
    };
    const removeOperator = (opId) => mutateMember('operators', { operator_id: opId, remove: true, end_date: effDate || null });
    const addSupervisor = () => {
        if (!addSvId) return;
        mutateMember('supervisors', { supervisor_id: Number(addSvId), start_date: effDate || null });
        setAddSvId('');
    };
    const removeSupervisor = (svId) => mutateMember('supervisors', { supervisor_id: svId, remove: true, end_date: effDate || null });

    const memberOpIds = new Set((members.operators || []).map((o) => o.id));
    const memberSvIds = new Set((members.supervisors || []).map((s) => s.id));

    return (
        <div className="p-4 sm:p-6" style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h2 className="text-xl font-semibold text-slate-900">Группы</h2>
                    <p className="text-[13px] text-slate-500">Историческая принадлежность операторов и СВ + модель расчёта группы.</p>
                </div>
                <button className={iosBtnPrimary} onClick={openCreate}>
                    <FaIcon className="fas fa-plus" /> Создать группу
                </button>
            </div>

            <div className="mb-4 flex flex-wrap items-center gap-3">
                <input
                    className={`${iosInput} max-w-xs`}
                    placeholder="Поиск по названию…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                />
                <label className="flex items-center gap-2 text-[13px] text-slate-600">
                    <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
                    Показывать архивные
                </label>
            </div>

            {loading ? (
                <div className="p-6 text-sm text-slate-500">Загрузка групп…</div>
            ) : visibleGroups.length === 0 ? (
                <div className={`${iosCard} p-6 text-sm text-slate-500`}>Групп нет.</div>
            ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {visibleGroups.map((g) => (
                        <div key={g.id} className={`${iosCard} p-4 flex flex-col gap-3 ${g.status === 'archived' ? 'opacity-70' : ''}`}>
                            <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0">
                                    <div className="truncate font-semibold text-slate-900" title={g.name}>{g.name}</div>
                                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                                        <IosBadge tone={MODEL_TONE[g.calculation_model_code] || 'slate'}>
                                            <FaIcon className={`fas ${g.calculation_model_code === 'chat_manager' ? 'fa-comments' : 'fa-headset'}`} />
                                            {g.calculation_model_name || modelName(g.calculation_model_code)}
                                        </IosBadge>
                                        {g.status === 'archived' && <IosBadge tone="amber">архив</IosBadge>}
                                    </div>
                                </div>
                            </div>
                            <div className="text-[12.5px] text-slate-500 space-y-0.5">
                                {g.direction_name && <div><FaIcon className="fas fa-sitemap mr-1" />{g.direction_name}</div>}
                                <div><FaIcon className="fas fa-users mr-1" />Операторов: {g.active_operators ?? 0}</div>
                                <div className="truncate">
                                    <FaIcon className="fas fa-user-tie mr-1" />
                                    {(g.supervisors || []).map((s) => s.name).join(', ') || 'без СВ'}
                                </div>
                            </div>
                            <div className="mt-auto flex flex-wrap gap-2 pt-1">
                                <button className={iosBtnSecondary} onClick={() => openMembers(g)}>
                                    <FaIcon className="fas fa-user-gear" /> Состав
                                </button>
                                {g.status === 'archived' ? (
                                    <button className={iosBtnGhost} onClick={() => setGroupArchived(g, false)}>
                                        <FaIcon className="fas fa-rotate-left" /> Вернуть
                                    </button>
                                ) : (
                                    <button className={iosBtnGhost} onClick={() => setGroupArchived(g, true)}>
                                        <FaIcon className="fas fa-box-archive" /> Архив
                                    </button>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* ─── Create modal ─── */}
            <IosModal
                open={createOpen}
                onClose={closeCreate}
                title="Новая группа"
                subtitle="Модель расчёта задаётся при создании и далее не меняется"
                footer={(
                    <>
                        <button className={iosBtnSecondary} onClick={closeCreate} disabled={saving}>Отмена</button>
                        <button className={iosBtnPrimary} onClick={() => submitCreate(suggestions.length > 0)} disabled={saving}>
                            {suggestions.length > 0 ? 'Создать всё равно' : 'Создать'}
                        </button>
                    </>
                )}
            >
                <div className="space-y-3">
                    <div>
                        <div className={iosGroupLabel}>Название</div>
                        <input className={iosInput} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Например: Группа продаж — Иванов" />
                    </div>
                    <div>
                        <div className={iosGroupLabel}>Отдел</div>
                        <select className={iosInput} value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value })}>
                            <option value="">— не задан —</option>
                            {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                        </select>
                    </div>
                    <div>
                        <div className={iosGroupLabel}>Направление (опционально)</div>
                        <select
                            className={iosInput}
                            value={form.direction_id}
                            onChange={(e) => {
                                const dirId = e.target.value;
                                const dir = directions.find((x) => String(x.id) === String(dirId));
                                setForm({
                                    ...form,
                                    direction_id: dirId,
                                    calculation_model_code: dir ? dirModelOf(dir) : form.calculation_model_code,
                                    department_id: form.department_id || (dir ? String(dir.department_id ?? dir.departmentId ?? '') : form.department_id),
                                });
                            }}
                        >
                            <option value="">— без направления —</option>
                            {directions.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                        </select>
                    </div>
                    <div>
                        <div className={iosGroupLabel}>Модель расчёта</div>
                        <select className={iosInput} value={form.calculation_model_code} onChange={(e) => setForm({ ...form, calculation_model_code: e.target.value })}>
                            {calcModels.map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
                        </select>
                    </div>

                    {suggestions.length > 0 && (
                        <div className={`${iosCard} p-3 border border-amber-200 bg-amber-50/50`}>
                            <div className="mb-2 text-[12.5px] font-medium text-amber-700">Архивные группы того же отдела/направления:</div>
                            <div className="space-y-1.5">
                                {suggestions.map((s) => (
                                    <div key={s.id} className="flex items-center justify-between gap-2 text-[13px]">
                                        <span className="truncate text-slate-700">{s.name}</span>
                                        <button className={iosBtnGhost} onClick={() => reuseSuggestion(s.id)} disabled={saving}>
                                            <FaIcon className="fas fa-rotate-left" /> Переиспользовать
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </IosModal>

            {/* ─── Members modal ─── */}
            <IosModal
                open={!!membersGroup}
                onClose={closeMembers}
                title={membersGroup ? `Состав: ${membersGroup.name}` : 'Состав группы'}
                subtitle="Дата вступления/исключения применяется к добавлению/удалению ниже"
                maxWidth="max-w-2xl"
            >
                <div className="space-y-4">
                    <div>
                        <div className={iosGroupLabel}>Дата изменения (необязательно, по умолчанию сегодня)</div>
                        <input type="date" className={`${iosInput} max-w-xs`} value={effDate} onChange={(e) => setEffDate(e.target.value)} />
                    </div>

                    {membersLoading ? (
                        <div className="p-4 text-sm text-slate-500">Загрузка состава…</div>
                    ) : (
                        <>
                            <section className="space-y-2">
                                <div className={iosGroupLabel}>Супервайзеры</div>
                                <div className="flex flex-wrap gap-1.5">
                                    {(members.supervisors || []).length === 0 && <span className="text-[13px] text-slate-400">нет</span>}
                                    {(members.supervisors || []).map((s) => (
                                        <IosBadge key={s.id} tone="blue">
                                            {s.name}
                                            <button className="ml-1 text-blue-500 hover:text-rose-600" onClick={() => removeSupervisor(s.id)} disabled={memberBusy} title="Открепить">×</button>
                                        </IosBadge>
                                    ))}
                                </div>
                                <div className="flex gap-2">
                                    <select className={iosInput} value={addSvId} onChange={(e) => setAddSvId(e.target.value)}>
                                        <option value="">+ добавить супервайзера…</option>
                                        {supervisorsList.filter((s) => !memberSvIds.has(s.id)).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                                    </select>
                                    <button className={iosBtnSecondary} onClick={addSupervisor} disabled={!addSvId || memberBusy}>Добавить</button>
                                </div>
                            </section>

                            <section className="space-y-2">
                                <div className={iosGroupLabel}>Операторы ({(members.operators || []).length})</div>
                                <div className="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
                                    {(members.operators || []).length === 0 && <span className="text-[13px] text-slate-400">нет</span>}
                                    {(members.operators || []).map((o) => (
                                        <IosBadge key={o.id} tone="slate">
                                            {o.name}
                                            <button className="ml-1 text-slate-400 hover:text-rose-600" onClick={() => removeOperator(o.id)} disabled={memberBusy} title="Исключить">×</button>
                                        </IosBadge>
                                    ))}
                                </div>
                                <div className="flex gap-2">
                                    <select className={iosInput} value={addOpId} onChange={(e) => setAddOpId(e.target.value)}>
                                        <option value="">+ добавить оператора…</option>
                                        {operatorsList.filter((o) => !memberOpIds.has(o.id)).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                                    </select>
                                    <button className={iosBtnSecondary} onClick={addOperator} disabled={!addOpId || memberBusy}>Добавить</button>
                                </div>
                                <p className="text-[11.5px] text-slate-400">
                                    Перевод оператора в эту группу автоматически закрывает его прошлую основную группу.
                                </p>
                            </section>
                        </>
                    )}
                </div>
            </IosModal>
        </div>
    );
};

export default GroupsView;
