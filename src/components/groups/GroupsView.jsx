import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import FaIcon from '../common/FaIcon';
import { normalizeRole } from '../../utils/roles';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosModal,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

// Модель → тон бейджа. Источник истины моделей — каталог с бэка (calculation_models).
const MODEL_TONE = { operator: 'blue', chat_manager: 'green', tez_line: 'amber', tez_op: 'amber' };
const FALLBACK_MODELS = [
    { code: 'operator', name: 'Операторская модель' },
    { code: 'chat_manager', name: 'Модель чат-менеджера' },
];
const EMPTY_FORM = { name: '', department_id: '', direction_id: '', calculation_model_code: 'operator' };

// Уволенные не должны попадать в селекторы добавления и в активный состав.
const FIRED_STATUSES = new Set(['fired', 'dismissal']);
const isFiredStatus = (u) => FIRED_STATUSES.has(String(u?.status || '').toLowerCase());

// Человекочитаемая подпись месяца: "2026-05" -> "Май 2026"
const MONTHS_RU_NOM = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const monthLabelRu = (ym) => {
    const m = /^(\d{4})-(\d{2})$/.exec(String(ym || ''));
    if (!m) return ym || '';
    return `${MONTHS_RU_NOM[Number(m[2]) - 1] || m[2]} ${m[1]}`;
};

// «2026-06-01» → «01.06.2026» (дата вступления в группу).
const fmtDate = (ymd) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(ymd || ''));
    return m ? `${m[3]}.${m[2]}.${m[1]}` : '';
};

// Дата вступления — только прошлое/сегодня (бэк это же и валидирует).
const todayYmd = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

// ISO → «27.06.2026, 15:40» (для журнала смены модели).
const fmtDateTime = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
};

const GroupsView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader }) => {
    const [groups, setGroups] = useState([]);
    const [directions, setDirections] = useState([]);
    const [calcModels, setCalcModels] = useState([]);
    const [departments, setDepartments] = useState([]);
    const [users, setUsers] = useState([]);
    const [supervisors, setSupervisors] = useState([]);
    const [loading, setLoading] = useState(true);
    const [showArchived, setShowArchived] = useState(false);
    const [search, setSearch] = useState('');

    // create modal
    const [createOpen, setCreateOpen] = useState(false);
    const [form, setForm] = useState({ ...EMPTY_FORM });
    const [suggestions, setSuggestions] = useState([]);
    const [saving, setSaving] = useState(false);

    // rename modal
    const [renameGroup, setRenameGroup] = useState(null);
    const [renameName, setRenameName] = useState('');

    // model change / rollback modal
    const [modelGroup, setModelGroup] = useState(null);
    const [newModelCode, setNewModelCode] = useState('operator');
    const [modelHistory, setModelHistory] = useState([]);
    const [modelHistoryLoading, setModelHistoryLoading] = useState(false);
    const [modelBusy, setModelBusy] = useState(false);

    // members modal
    const [membersGroup, setMembersGroup] = useState(null);
    const [members, setMembers] = useState({ operators: [], supervisors: [] });
    const [membersLoading, setMembersLoading] = useState(false);
    const [addOpId, setAddOpId] = useState('');
    const [addSvId, setAddSvId] = useState('');
    const [effDate, setEffDate] = useState('');
    const [memberBusy, setMemberBusy] = useState(false);
    // Инлайн-правка даты вступления: { kind: 'operator'|'supervisor', id, value }
    const [dateEdit, setDateEdit] = useState(null);
    // Исторический состав: '' = текущий (живой), 'YYYY-MM' = замороженный снимок месяца
    const [membersMonth, setMembersMonth] = useState('');
    const [snap, setSnap] = useState(null);
    const [snapLoading, setSnapLoading] = useState(false);

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
            // Супервайзеров берём из /api/admin/sv_list — он, в отличие от /api/admin/users,
            // отдаёт СВ и обычным админам/супер-админам (а не только главам отделов).
            const [d, u, dep, sv] = await Promise.all([
                api('/api/admin/directions'),
                api('/api/admin/users'),
                api('/api/admin/departments'),
                api('/api/admin/sv_list'),
            ]);
            if (d.ok) {
                setDirections(d.data.directions || []);
                setCalcModels((d.data.calculation_models || []).length ? d.data.calculation_models : FALLBACK_MODELS);
            } else {
                setCalcModels(FALLBACK_MODELS);
            }
            if (u.ok) setUsers(u.data.users || u.data.operators || (Array.isArray(u.data) ? u.data : []));
            if (dep.ok) setDepartments(dep.data.departments || []);
            if (sv.ok) setSupervisors(sv.data.sv_list || []);
        } catch {
            setCalcModels(FALLBACK_MODELS);
        }
    }, [api]);

    useEffect(() => { fetchGroups(); fetchAux(); }, [fetchGroups, fetchAux]);

    const dirModelOf = (dir) => String(dir?.calculationModelCode || dir?.calculation_model_code || 'operator');

    // Кандидаты на добавление сужаются до отдела открытой группы (как видит глава отдела).
    // Группа без отдела — без сужения; запись без department_id в скоуп не попадает.
    const membersDeptId = membersGroup?.department_id ?? null;
    const sameGroupDept = (u) => {
        if (membersDeptId == null) return true;
        const d = u?.department_id ?? u?.departmentId;
        return d != null && Number(d) === Number(membersDeptId);
    };

    const operatorsList = useMemo(
        () => (users || []).filter((u) => ['operator', 'trainee'].includes(normalizeRole(u.role)) && !isFiredStatus(u) && sameGroupDept(u)),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [users, membersDeptId]
    );
    const supervisorsList = useMemo(
        () => (supervisors || []).filter((s) => ['sv', 'supervisor'].includes(normalizeRole(s.role)) && !isFiredStatus(s) && sameGroupDept(s)),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [supervisors, membersDeptId]
    );
    const modelName = (code) => (calcModels.find((m) => m.code === code) || {}).name || code;

    const visibleGroups = useMemo(() => {
        const q = search.trim().toLowerCase();
        return (groups || [])
            .filter((g) => showArchived || g.status !== 'archived')
            .filter((g) => !q || (g.name || '').toLowerCase().includes(q));
    }, [groups, showArchived, search]);

    const deptName = (id) => {
        if (id == null || id === '__none__') return 'Без отдела';
        const d = (departments || []).find((x) => String(x.id) === String(id));
        return d ? d.name : `Отдел #${id}`;
    };

    // Группы, сгруппированные по отделам (для секций списка).
    const groupedByDept = useMemo(() => {
        const map = new Map();
        for (const g of visibleGroups) {
            const key = g.department_id == null ? '__none__' : String(g.department_id);
            if (!map.has(key)) map.set(key, []);
            map.get(key).push(g);
        }
        const entries = Array.from(map.entries());
        entries.sort((a, b) => {
            if (a[0] === '__none__') return 1;
            if (b[0] === '__none__') return -1;
            return deptName(a[0]).localeCompare(deptName(b[0]), 'ru');
        });
        return entries;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [visibleGroups, departments]);

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

    /* ─── rename ─── */
    const openRename = (g) => { setRenameGroup(g); setRenameName(g.name || ''); };
    const closeRename = () => { setRenameGroup(null); setRenameName(''); };
    const submitRename = async () => {
        const name = renameName.trim();
        if (!name) { showToastRef.current?.('Укажите название группы', 'error'); return; }
        if (name === renameGroup.name) { closeRename(); return; }
        setSaving(true);
        try {
            const { ok, data } = await api(`/api/admin/groups/${renameGroup.id}/rename`, { method: 'POST', body: { name } });
            if (ok && data.group) {
                setGroups((prev) => prev.map((g) => (g.id === renameGroup.id ? data.group : g)));
                showToastRef.current?.('Группа переименована', 'success');
                closeRename();
            } else {
                showToastRef.current?.(data.error || 'Не удалось переименовать группу', 'error');
            }
        } catch {
            showToastRef.current?.('Ошибка сети', 'error');
        } finally { setSaving(false); }
    };

    /* ─── model change / rollback ─── */
    const openModel = async (g) => {
        setModelGroup(g);
        setNewModelCode(g.calculation_model_code || 'operator');
        setModelHistory([]);
        setModelHistoryLoading(true);
        try {
            const { ok, data } = await api(`/api/admin/groups/${g.id}/model_history`);
            if (ok) setModelHistory(data.history || []);
        } finally {
            setModelHistoryLoading(false);
        }
    };
    const closeModel = () => { setModelGroup(null); setModelHistory([]); setModelBusy(false); };

    // Применяет обновлённую группу в списке и в открытой модалке.
    const applyGroupUpdate = (grp) => {
        if (!grp) return;
        setGroups((prev) => prev.map((g) => (g.id === grp.id ? grp : g)));
        setModelGroup((prev) => (prev && prev.id === grp.id ? grp : prev));
    };

    const reloadModelHistory = async (groupId) => {
        const { ok, data } = await api(`/api/admin/groups/${groupId}/model_history`);
        if (ok) setModelHistory(data.history || []);
    };

    const submitModelChange = async () => {
        if (!modelGroup) return;
        if (newModelCode === modelGroup.calculation_model_code) { closeModel(); return; }
        setModelBusy(true);
        try {
            const { ok, data } = await api(`/api/admin/groups/${modelGroup.id}/model`, {
                method: 'POST', body: { calculation_model_code: newModelCode },
            });
            if (ok && data.group) {
                applyGroupUpdate(data.group);
                showToastRef.current?.(data.changed ? 'Модель группы изменена' : 'Модель не изменилась', data.changed ? 'success' : 'info');
                await reloadModelHistory(modelGroup.id);
            } else {
                showToastRef.current?.(data.error || 'Не удалось сменить модель', 'error');
            }
        } catch {
            showToastRef.current?.('Ошибка сети', 'error');
        } finally { setModelBusy(false); }
    };

    const revertModel = async (targetCode) => {
        if (!modelGroup) return;
        setModelBusy(true);
        try {
            const { ok, data } = await api(`/api/admin/groups/${modelGroup.id}/model/revert`, {
                method: 'POST', body: targetCode ? { target_model_code: targetCode } : {},
            });
            if (ok && data.group) {
                applyGroupUpdate(data.group);
                setNewModelCode(data.group.calculation_model_code);
                showToastRef.current?.('Модель откачена', 'success');
                await reloadModelHistory(modelGroup.id);
            } else {
                showToastRef.current?.(data.error || 'Не удалось откатить модель', 'error');
            }
        } catch {
            showToastRef.current?.('Ошибка сети', 'error');
        } finally { setModelBusy(false); }
    };

    /* ─── members ─── */
    const openMembers = async (group) => {
        setMembersGroup(group);
        setAddOpId(''); setAddSvId(''); setEffDate('');
        setMembersMonth(''); setSnap(null); setDateEdit(null);
        setMembersLoading(true);
        try {
            const { ok, data } = await api(`/api/admin/groups/${group.id}/members`);
            if (ok) setMembers({ operators: data.operators || [], supervisors: data.supervisors || [] });
            else showToastRef.current?.(data.error || 'Не удалось загрузить состав', 'error');
        } finally {
            setMembersLoading(false);
        }
    };
    const closeMembers = () => { setMembersGroup(null); setMembers({ operators: [], supervisors: [] }); setMembersMonth(''); setSnap(null); setDateEdit(null); };

    // Последние 12 месяцев + «текущий» для исторического состава за выбранный месяц
    const monthOptions = useMemo(() => {
        const opts = [{ value: '', label: 'Текущий состав' }];
        const now = new Date();
        for (let i = 0; i < 12; i++) {
            const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
            const ym = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
            opts.push({ value: ym, label: monthLabelRu(ym) });
        }
        return opts;
    }, []);

    const selectMembersMonth = async (month) => {
        setMembersMonth(month);
        setDateEdit(null);
        if (!month || !membersGroup) { setSnap(null); return; }
        setSnapLoading(true);
        try {
            const { ok, data } = await api(`/api/admin/snapshots?month=${month}&group_id=${membersGroup.id}`);
            if (ok) setSnap({ ...(data.groups?.[0] || { operators: [], supervisor_names: [] }), frozen: !!data.frozen, closed: !!data.closed });
            else { setSnap(null); showToastRef.current?.(data.error || 'Не удалось загрузить снимок', 'error'); }
        } finally { setSnapLoading(false); }
    };

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

    /* ─── дата вступления участника (инлайн, прямо в строке состава) ─── */
    const isEditingDate = (kind, id) => !!dateEdit && dateEdit.kind === kind && dateEdit.id === id;
    const toggleDateEdit = (kind, m) => setDateEdit(
        isEditingDate(kind, m.id) ? null : { kind, id: m.id, value: m.start_date || '' }
    );

    const submitDateEdit = async () => {
        if (!dateEdit || !membersGroup) return;
        const value = (dateEdit.value || '').trim();
        if (!value) { showToastRef.current?.('Укажите дату вступления', 'error'); return; }
        setMemberBusy(true);
        try {
            const { ok, data } = await api(`/api/admin/groups/${membersGroup.id}/member_start_date`, {
                method: 'POST',
                body: { kind: dateEdit.kind, member_id: dateEdit.id, start_date: value },
            });
            if (ok) {
                setDateEdit(null);
                showToastRef.current?.(
                    data.changed ? `Дата вступления изменена на ${fmtDate(value)}` : 'Дата не изменилась',
                    data.changed ? 'success' : 'info'
                );
                await refreshMembers(membersGroup.id);
            } else {
                // Отказ показываем прямо в редакторе: тост живёт 5 секунд, а это
                // причина, которую нужно прочитать и исправить дату.
                setDateEdit((prev) => (prev ? { ...prev, error: data.error || 'Не удалось изменить дату' } : prev));
            }
        } catch {
            showToastRef.current?.('Ошибка сети', 'error');
        } finally {
            setMemberBusy(false);
        }
    };

    const memberOpIds = new Set((members.operators || []).map((o) => o.id));
    const memberSvIds = new Set((members.supervisors || []).map((s) => s.id));

    // Деление состава по статусу: уволенные отдельно от активных.
    const isFiredMember = (o) => isFiredStatus(o);
    const opsActive = (members.operators || []).filter((o) => !isFiredMember(o));
    const opsFired = (members.operators || []).filter((o) => isFiredMember(o));

    // Чип с датой вступления — он же кнопка инлайн-редактора.
    const dateChip = (kind, m) => {
        const editing = isEditingDate(kind, m.id);
        return (
            <button
                type="button"
                onClick={() => toggleDateEdit(kind, m)}
                title="Изменить дату вступления в группу"
                className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-medium transition ${
                    editing
                        ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-200'
                        : m.start_date
                            ? 'bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-700'
                            : 'bg-amber-50 text-amber-700 ring-1 ring-amber-100 hover:bg-amber-100'
                }`}
            >
                <FaIcon className="fas fa-calendar-day" />
                <span className="tabular-nums">{m.start_date ? `с ${fmtDate(m.start_date)}` : 'дата не указана'}</span>
                <FaIcon className={`fas fa-pen text-[9px] transition-opacity ${editing ? 'opacity-100' : 'opacity-0 group-hover:opacity-50'}`} />
            </button>
        );
    };

    // Редактор даты раскрывается прямо под строкой участника — не уводя в отдельную модалку.
    const dateEditor = (kind, m) => {
        if (!isEditingDate(kind, m.id)) return null;
        return (
            <div className="border-t border-slate-100 bg-slate-50/80 px-3 py-2.5 pl-10">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[11.5px] font-medium text-slate-500">В группе с</span>
                    <input
                        type="date"
                        autoFocus
                        max={todayYmd()}
                        className={`rounded-lg bg-white px-2.5 py-1.5 text-[13px] text-slate-900 ring-1 transition focus:outline-none focus:ring-2 ${
                            dateEdit.error ? 'ring-rose-300 focus:ring-rose-400' : 'ring-slate-200 focus:ring-blue-500/70'
                        }`}
                        value={dateEdit.value}
                        onChange={(e) => setDateEdit({ ...dateEdit, value: e.target.value, error: null })}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') submitDateEdit();
                            if (e.key === 'Escape') setDateEdit(null);
                        }}
                    />
                    <button
                        className={`${iosBtnPrimary} !px-3.5 !py-1.5 !text-[12.5px]`}
                        onClick={submitDateEdit}
                        disabled={memberBusy || !dateEdit.value}
                    >
                        Сохранить
                    </button>
                    <button className={`${iosBtnGhost} !px-2.5 !py-1.5 !text-[12.5px]`} onClick={() => setDateEdit(null)} disabled={memberBusy}>
                        Отмена
                    </button>
                </div>
                {dateEdit.error && (
                    <div className="mt-2 flex items-start gap-2 rounded-lg bg-rose-50 px-2.5 py-2 text-[12px] leading-snug text-rose-700 ring-1 ring-rose-100">
                        <FaIcon className="fas fa-triangle-exclamation mt-px shrink-0" />
                        <span>{dateEdit.error}</span>
                    </div>
                )}
            </div>
        );
    };

    const renderMemberRow = ({ kind, m, idx = null, icon = null, onRemove, removeTitle }) => (
        <div key={`${kind}-${m.id}`} className={isEditingDate(kind, m.id) ? 'bg-blue-50/30' : ''}>
            <div className="group flex items-center gap-2 px-3 py-2 transition hover:bg-slate-50">
                <span className="flex min-w-0 flex-1 items-center gap-2 text-[13.5px] text-slate-700">
                    {idx === null
                        ? <FaIcon className={`${icon} shrink-0 text-slate-400`} />
                        : <span className="w-5 shrink-0 text-right text-[12px] tabular-nums text-slate-300">{idx + 1}</span>}
                    <span className="truncate">{m.name}</span>
                </span>
                {dateChip(kind, m)}
                <button
                    className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
                    onClick={onRemove}
                    disabled={memberBusy}
                    title={removeTitle}
                >
                    <FaIcon className="fas fa-xmark" />
                </button>
            </div>
            {dateEditor(kind, m)}
        </div>
    );

    const renderOpRow = (o, idx) => renderMemberRow({
        kind: 'operator', m: o, idx,
        onRemove: () => removeOperator(o.id),
        removeTitle: 'Исключить',
    });

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
                <div className="space-y-6">
                {groupedByDept.map(([deptKey, list]) => (
                    <section key={deptKey}>
                        <div className="mb-2 flex items-center gap-2">
                            <FaIcon className="fas fa-layer-group text-slate-400" />
                            <h3 className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">{deptKey === '__none__' ? 'Без отдела' : deptName(deptKey)}</h3>
                            <span className="text-[12px] text-slate-400">· {list.length}</span>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {list.map((g) => (
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
                                <button className={iosBtnGhost} onClick={() => openRename(g)}>
                                    <FaIcon className="fas fa-pen" /> Переименовать
                                </button>
                                <button className={iosBtnGhost} onClick={() => openModel(g)}>
                                    <FaIcon className="fas fa-calculator" /> Модель
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
                    </section>
                ))}
                </div>
            )}

            {/* ─── Create modal ─── */}
            <IosModal
                open={createOpen}
                onClose={closeCreate}
                title="Новая группа"
                subtitle="Модель расчёта можно сменить позже кнопкой «Модель» (с возможностью отката)"
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
                        <CustomSelect
                            value={form.department_id}
                            onChange={(v) => setForm({ ...form, department_id: v })}
                            placeholder="— не задан —"
                            options={[{ value: '', label: '— не задан —' }, ...departments.map((d) => ({ value: String(d.id), label: d.name }))]}
                        />
                    </div>
                    <div>
                        <div className={iosGroupLabel}>Направление (опционально)</div>
                        <CustomSelect
                            value={form.direction_id}
                            placeholder="— без направления —"
                            onChange={(v) => {
                                const dir = directions.find((x) => String(x.id) === String(v));
                                setForm({
                                    ...form,
                                    direction_id: v,
                                    calculation_model_code: dir ? dirModelOf(dir) : form.calculation_model_code,
                                    department_id: form.department_id || (dir ? String(dir.department_id ?? dir.departmentId ?? '') : form.department_id),
                                });
                            }}
                            options={[{ value: '', label: '— без направления —' }, ...directions.map((d) => ({ value: String(d.id), label: d.name }))]}
                        />
                    </div>
                    <div>
                        <div className={iosGroupLabel}>Модель расчёта</div>
                        <CustomSelect
                            value={form.calculation_model_code}
                            onChange={(v) => setForm({ ...form, calculation_model_code: v })}
                            options={calcModels.map((m) => ({ value: m.code, label: m.name }))}
                        />
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

            {/* ─── Rename modal ─── */}
            <IosModal
                open={!!renameGroup}
                onClose={closeRename}
                title="Переименовать группу"
                subtitle="Меняется только название. Модель, отдел и направление неизменны."
                footer={(
                    <>
                        <button className={iosBtnSecondary} onClick={closeRename} disabled={saving}>Отмена</button>
                        <button className={iosBtnPrimary} onClick={submitRename} disabled={saving}>Сохранить</button>
                    </>
                )}
            >
                <div className="space-y-3">
                    <div>
                        <div className={iosGroupLabel}>Название</div>
                        <input className={iosInput} value={renameName} onChange={(e) => setRenameName(e.target.value)} autoFocus />
                    </div>
                </div>
            </IosModal>

            {/* ─── Model change / rollback modal ─── */}
            <IosModal
                open={!!modelGroup}
                onClose={closeModel}
                title={modelGroup ? `Модель расчёта: ${modelGroup.name}` : 'Модель расчёта'}
                subtitle="Смену модели можно откатить — учёт часов и закрытые месяцы не теряются"
                maxWidth="max-w-xl"
                footer={(
                    <>
                        <button className={iosBtnSecondary} onClick={closeModel} disabled={modelBusy}>Закрыть</button>
                        <button
                            className={iosBtnPrimary}
                            onClick={submitModelChange}
                            disabled={modelBusy || !modelGroup || newModelCode === modelGroup?.calculation_model_code}
                        >
                            Сменить модель
                        </button>
                    </>
                )}
            >
                {modelGroup && (
                <div className="space-y-4">
                    <div>
                        <div className={iosGroupLabel}>Текущая модель</div>
                        <IosBadge tone={MODEL_TONE[modelGroup.calculation_model_code] || 'slate'}>
                            {modelGroup.calculation_model_name || modelName(modelGroup.calculation_model_code)}
                        </IosBadge>
                    </div>
                    <div>
                        <div className={iosGroupLabel}>Новая модель</div>
                        <CustomSelect
                            value={newModelCode}
                            onChange={setNewModelCode}
                            options={calcModels.map((m) => ({ value: m.code, label: m.name }))}
                        />
                    </div>
                    {newModelCode !== modelGroup.calculation_model_code && (
                        <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-[12.5px] text-amber-700">
                            <FaIcon className="fas fa-triangle-exclamation mr-1" />
                            Модель влияет на метрики и расчёт зарплаты для незакрытых месяцев. Закрытые (замороженные) месяцы не изменятся. Если передумаете — изменение можно откатить ниже, данные не потеряются.
                        </div>
                    )}

                    <section className="space-y-2">
                        <div className={iosGroupLabel}>История изменений модели</div>
                        {modelHistoryLoading ? (
                            <div className="p-3 text-sm text-slate-500">Загрузка истории…</div>
                        ) : modelHistory.length === 0 ? (
                            <div className="p-3 text-[13px] text-slate-400">Модель ещё не меняли.</div>
                        ) : (
                            <div className="rounded-xl ring-1 ring-slate-200 bg-white divide-y divide-slate-100 overflow-hidden max-h-64 overflow-y-auto">
                                {modelHistory.map((h) => (
                                    <div key={h.id} className="flex items-center justify-between gap-2 px-3 py-2 text-[13px]">
                                        <div className="min-w-0">
                                            <div className="text-slate-700">
                                                {(h.old_model_name || h.old_model_code || '—')} → <span className="font-medium">{h.new_model_name || h.new_model_code}</span>
                                                {h.is_revert ? <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500">откат</span> : null}
                                            </div>
                                            <div className="text-[11.5px] text-slate-400">
                                                {fmtDateTime(h.created_at)}{h.changed_by_name ? ` · ${h.changed_by_name}` : ''}
                                            </div>
                                        </div>
                                        {h.old_model_code && h.old_model_code !== modelGroup.calculation_model_code ? (
                                            <button
                                                className={`${iosBtnGhost} shrink-0`}
                                                onClick={() => revertModel(h.old_model_code)}
                                                disabled={modelBusy}
                                                title={`Вернуть модель «${h.old_model_name || h.old_model_code}»`}
                                            >
                                                <FaIcon className="fas fa-rotate-left" /> Откатить
                                            </button>
                                        ) : null}
                                    </div>
                                ))}
                            </div>
                        )}
                    </section>
                </div>
                )}
            </IosModal>

            {/* ─── Members modal ─── */}
            <IosModal
                open={!!membersGroup}
                onClose={closeMembers}
                title={membersGroup ? `Состав: ${membersGroup.name}` : 'Состав группы'}
                subtitle="Дата у каждого участника — когда он вошёл в группу; нажмите на неё, чтобы поправить"
                maxWidth="max-w-2xl"
            >
                <div className="space-y-4">
                    <div>
                        <div className={iosGroupLabel}>Месяц состава</div>
                        <CustomSelect className="max-w-xs" value={membersMonth} onChange={selectMembersMonth} options={monthOptions} />
                        {membersMonth ? (
                            <div className="mt-1 text-[12px] text-amber-700">Исторический состав за {monthLabelRu(membersMonth)} — только просмотр (как было тогда).</div>
                        ) : null}
                    </div>

                    {membersMonth ? (
                        snapLoading ? (
                            <div className="p-4 text-sm text-slate-500">Загрузка снимка…</div>
                        ) : (!snap || (snap.operators || []).length === 0) ? (
                            <div className="p-4 text-sm text-slate-500">{snap && snap.frozen ? 'В этом месяце в группе никого не было.' : 'Снимок за этот месяц ещё не создан (месяц не закрыт или ещё не открывался в учёте часов).'}</div>
                        ) : (
                            <>
                                <section className="space-y-2">
                                    <div className={iosGroupLabel}>Супервайзеры (на тот месяц)</div>
                                    <div className="rounded-xl ring-1 ring-slate-200 bg-white px-3 py-2 text-[13.5px] text-slate-700">
                                        {(snap.supervisor_names || []).length ? (snap.supervisor_names || []).join(', ') : 'нет'}
                                    </div>
                                </section>
                                <section className="space-y-2">
                                    <div className={iosGroupLabel}>Операторы ({(snap.operators || []).length}) — состав на {monthLabelRu(membersMonth)}</div>
                                    <div className="rounded-xl ring-1 ring-slate-200 bg-white divide-y divide-slate-100 overflow-hidden max-h-80 overflow-y-auto">
                                        {(snap.operators || []).map((o, idx) => (
                                            <div key={o.operator_id} className="flex items-center justify-between gap-2 px-3 py-2">
                                                <span className="flex min-w-0 items-center gap-2 text-[13.5px] text-slate-700">
                                                    <span className="text-slate-400 tabular-nums">{idx + 1}.</span>
                                                    <span className="truncate">{o.name}</span>
                                                    {(o.first_day || o.last_day) ? (
                                                        <span className="text-[11px] text-slate-400 shrink-0">дни {String(o.first_day || '').slice(8)}–{String(o.last_day || '').slice(8)}</span>
                                                    ) : null}
                                                </span>
                                                <span className="flex items-center gap-1.5 shrink-0 text-[11px]">
                                                    {FIRED_STATUSES.has(String(o.status)) ? (<span className="rounded-full bg-rose-50 px-2 py-0.5 text-rose-600">уволен</span>) : null}
                                                    {o.role && o.role !== 'operator' ? (<span className="rounded-full bg-violet-50 px-2 py-0.5 text-violet-600">{o.role}</span>) : null}
                                                    <span className={`rounded-full px-2 py-0.5 ${o.calculation_model_code === 'chat_manager' ? 'bg-emerald-50 text-emerald-600' : 'bg-blue-50 text-blue-600'}`}>{o.calculation_model_code === 'chat_manager' ? 'чат' : 'оператор'}</span>
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </section>
                            </>
                        )
                    ) : (
                    <>
                    <div>
                        <div className={iosGroupLabel}>Дата для добавления / исключения (по умолчанию сегодня)</div>
                        <input type="date" className={`${iosInput} max-w-xs`} value={effDate} onChange={(e) => setEffDate(e.target.value)} />
                        <p className="mt-1 px-1 text-[11.5px] text-slate-400">
                            Применяется к кнопкам «Добавить» и «✕» ниже. Дату уже состоящего в группе участника меняйте чипом с датой в его строке.
                        </p>
                    </div>

                    {membersLoading ? (
                        <div className="p-4 text-sm text-slate-500">Загрузка состава…</div>
                    ) : (
                        <>
                            <section className="space-y-2">
                                <div className={iosGroupLabel}>Супервайзеры</div>
                                <div className="rounded-xl ring-1 ring-slate-200 bg-white divide-y divide-slate-100 overflow-hidden">
                                    {(members.supervisors || []).length === 0 ? (
                                        <div className="px-3 py-2 text-[13px] text-slate-400">нет</div>
                                    ) : (members.supervisors || []).map((s) => renderMemberRow({
                                        kind: 'supervisor', m: s, icon: 'fas fa-user-tie',
                                        onRemove: () => removeSupervisor(s.id),
                                        removeTitle: 'Открепить',
                                    }))}
                                </div>
                                <div className="flex items-center gap-2">
                                    <CustomSelect
                                        className="flex-1"
                                        searchable
                                        value={addSvId}
                                        onChange={(v) => setAddSvId(v)}
                                        placeholder="+ добавить супервайзера…"
                                        searchPlaceholder="Поиск супервайзера…"
                                        options={supervisorsList.filter((s) => !memberSvIds.has(s.id)).map((s) => ({ value: String(s.id), label: s.name }))}
                                    />
                                    <button className={iosBtnSecondary} onClick={addSupervisor} disabled={!addSvId || memberBusy}>Добавить</button>
                                </div>
                            </section>

                            <section className="space-y-2">
                                <div className={iosGroupLabel}>Операторы ({(members.operators || []).length})</div>
                                <div className="space-y-3 max-h-80 overflow-y-auto pr-0.5">
                                    <div>
                                        <div className="px-1 pb-1 text-[11.5px] font-semibold text-emerald-600">Активные · {opsActive.length}</div>
                                        <div className="rounded-xl ring-1 ring-slate-200 bg-white divide-y divide-slate-100 overflow-hidden">
                                            {opsActive.length === 0 ? (
                                                <div className="px-3 py-2 text-[13px] text-slate-400">нет</div>
                                            ) : opsActive.map((o, idx) => renderOpRow(o, idx))}
                                        </div>
                                    </div>
                                    {opsFired.length > 0 && (
                                        <div>
                                            <div className="px-1 pb-1 text-[11.5px] font-semibold text-rose-500">Уволенные · {opsFired.length}</div>
                                            <div className="rounded-xl ring-1 ring-rose-100 bg-rose-50/30 divide-y divide-rose-100/70 overflow-hidden">
                                                {opsFired.map((o, idx) => renderOpRow(o, idx))}
                                            </div>
                                        </div>
                                    )}
                                </div>
                                <div className="flex items-center gap-2">
                                    <CustomSelect
                                        className="flex-1"
                                        searchable
                                        value={addOpId}
                                        onChange={(v) => setAddOpId(v)}
                                        placeholder="+ добавить оператора…"
                                        searchPlaceholder="Поиск оператора…"
                                        options={operatorsList.filter((o) => !memberOpIds.has(o.id)).map((o) => ({ value: String(o.id), label: o.name }))}
                                    />
                                    <button className={iosBtnSecondary} onClick={addOperator} disabled={!addOpId || memberBusy}>Добавить</button>
                                </div>
                                <p className="text-[11.5px] text-slate-400">
                                    Перевод оператора в эту группу автоматически закрывает его прошлую основную группу.
                                    Дату вступления можно сдвинуть только по дням без учтённых часов: если за период уже есть данные, правка блокируется — чтобы опечатка в дате не унесла часы из группы, где их вели.
                                </p>
                            </section>
                        </>
                    )}
                    </>
                    )}
                </div>
            </IosModal>
        </div>
    );
};

export default GroupsView;
