import React, { useState, useEffect, useCallback, useMemo } from 'react';
import FaIcon from '../common/FaIcon';
import { normalizeRole } from '../../utils/roles';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosToggle, IosBadge, IosModal,
} from '../ui/ios';

const EMPTY_FORM = { code: '', name: '', description: '', is_active: true };

const ROLE_LABELS = {
    super_admin: 'Супер-админ', admin: 'Админ', sv: 'Супервайзер',
    trainer: 'Тренер', operator: 'Оператор', trainee: 'Стажёр',
};
const roleLabel = (role) => ROLE_LABELS[normalizeRole(role)] || role || '—';

const DepartmentsView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader }) => {
    const isSuperAdmin = normalizeRole(user?.role) === 'super_admin';

    const [departments, setDepartments] = useState([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [search, setSearch] = useState('');

    // create / edit modal
    const [formOpen, setFormOpen] = useState(false);
    const [editingId, setEditingId] = useState(null);
    const [form, setForm] = useState({ ...EMPTY_FORM });

    // users (for head selector)
    const [users, setUsers] = useState([]);

    // head assignment modal
    const [headDept, setHeadDept] = useState(null);
    const [headQuery, setHeadQuery] = useState('');
    const [headSaving, setHeadSaving] = useState(false);

    // head history modal
    const [historyDept, setHistoryDept] = useState(null);
    const [history, setHistory] = useState([]);
    const [historyLoading, setHistoryLoading] = useState(false);

    const authHeaders = useCallback(
        (extra = {}) => withAccessTokenHeader({ 'X-User-Id': String(user.id), ...extra }),
        [withAccessTokenHeader, user?.id]
    );

    const fetchDepartments = useCallback(async () => {
        setLoading(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/admin/departments`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.departments) setDepartments(data.departments);
            else showToast?.(data.error || 'Не удалось загрузить отделы', 'error');
        } catch {
            showToast?.('Ошибка сети при загрузке отделов', 'error');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, showToast]);

    const fetchUsers = useCallback(async () => {
        try {
            const resp = await fetch(`${apiBaseUrl}/api/admin/users`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const data = await resp.json().catch(() => ({}));
            const list = data.users || data.operators || (Array.isArray(data) ? data : []);
            if (Array.isArray(list)) setUsers(list);
        } catch { /* мягко игнорируем — селектор главы просто будет пустым */ }
    }, [apiBaseUrl, authHeaders]);

    useEffect(() => { fetchDepartments(); fetchUsers(); }, [fetchDepartments, fetchUsers]);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!q) return departments;
        return departments.filter((d) =>
            (d.name || '').toLowerCase().includes(q)
            || (d.code || '').toLowerCase().includes(q)
            || (d.description || '').toLowerCase().includes(q)
        );
    }, [departments, search]);

    /* ─── create / edit ─── */
    const openCreate = () => { setEditingId(null); setForm({ ...EMPTY_FORM }); setFormOpen(true); };
    const openEdit = (dept) => {
        setEditingId(dept.id);
        setForm({ code: dept.code || '', name: dept.name || '', description: dept.description || '', is_active: dept.is_active !== false });
        setFormOpen(true);
    };
    const closeForm = () => { setFormOpen(false); setEditingId(null); setForm({ ...EMPTY_FORM }); };

    const submitForm = async (e) => {
        e?.preventDefault?.();
        if (!form.name.trim() || (!editingId && !form.code.trim())) {
            showToast?.('Код и название обязательны', 'error');
            return;
        }
        setSaving(true);
        try {
            const isEdit = !!editingId;
            const url = isEdit
                ? `${apiBaseUrl}/api/admin/departments/${editingId}`
                : `${apiBaseUrl}/api/admin/departments`;
            const body = isEdit
                ? { code: form.code.trim(), slug: form.code.trim(), name: form.name.trim(), description: form.description.trim() || null, is_active: form.is_active }
                : { code: form.code.trim(), name: form.name.trim(), description: form.description.trim() || null };
            const resp = await fetch(url, {
                method: isEdit ? 'PUT' : 'POST',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(body),
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.department) {
                setDepartments((prev) => isEdit
                    ? prev.map((d) => (d.id === editingId ? data.department : d))
                    : [...prev, data.department]);
                showToast?.(isEdit ? 'Отдел обновлён' : 'Отдел создан', 'success');
                closeForm();
            } else {
                showToast?.(data.error || 'Ошибка сохранения', 'error');
            }
        } catch {
            showToast?.('Ошибка сети', 'error');
        } finally {
            setSaving(false);
        }
    };

    /* ─── head assignment ─── */
    const headCandidates = useMemo(() => {
        if (!headDept) return [];
        const q = headQuery.trim().toLowerCase();
        return users
            .filter((u) => {
                // если в payload есть department_id — ограничиваем отделом; иначе показываем всех (бэкенд проверит)
                const dep = u.department_id ?? u.departmentId;
                if (dep != null && Number(dep) !== Number(headDept.id)) return false;
                if (!q) return true;
                return (u.name || '').toLowerCase().includes(q) || roleLabel(u.role).toLowerCase().includes(q);
            })
            .slice(0, 60);
    }, [users, headDept, headQuery]);

    const applyHead = async (userId) => {
        if (!headDept) return;
        setHeadSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/admin/departments/${headDept.id}/head`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ user_id: userId }),
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.department) {
                setDepartments((prev) => prev.map((d) => (d.id === headDept.id ? data.department : d)));
                showToast?.(userId ? 'Глава отдела назначена' : 'Глава отдела снята', 'success');
                setHeadDept(null);
                setHeadQuery('');
            } else {
                showToast?.(data.error || 'Не удалось изменить главу', 'error');
            }
        } catch {
            showToast?.('Ошибка сети', 'error');
        } finally {
            setHeadSaving(false);
        }
    };

    /* ─── head history ─── */
    const openHistory = async (dept) => {
        setHistoryDept(dept);
        setHistory([]);
        setHistoryLoading(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/admin/departments/${dept.id}/head/history`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.history) setHistory(data.history);
            else showToast?.(data.error || 'Не удалось загрузить историю', 'error');
        } catch {
            showToast?.('Ошибка сети', 'error');
        } finally {
            setHistoryLoading(false);
        }
    };

    const fmtDate = (iso) => {
        if (!iso) return '';
        try {
            return new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch { return iso; }
    };

    return (
        <div className="space-y-4" style={{ fontFamily: APPLE_FONT }}>
            {/* Header */}
            <div className="sticky top-0 z-10 -mx-1 rounded-2xl border border-slate-200/70 bg-white/80 px-4 py-3 backdrop-blur-xl">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-center gap-3">
                        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                            <FaIcon className="fa-solid fa-layer-group" />
                        </div>
                        <div>
                            <h2 className="text-[17px] font-semibold tracking-tight text-slate-900">Отделы</h2>
                            <p className="text-[12px] text-slate-400">Всего: {departments.length}</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <div className="relative">
                            <FaIcon className="fas fa-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" style={{ width: 13, height: 13 }} />
                            <input
                                type="text"
                                placeholder="Поиск отдела…"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                className={`${iosInput} w-48 pl-9`}
                            />
                        </div>
                        {isSuperAdmin && (
                            <button onClick={openCreate} className={iosBtnPrimary}>
                                <FaIcon className="fas fa-plus" />
                                <span className="hidden sm:inline">Новый отдел</span>
                            </button>
                        )}
                        <button onClick={fetchDepartments} disabled={loading} className={iosBtnGhost} title="Обновить">
                            <FaIcon className={`fas fa-sync-alt ${loading ? 'animate-spin' : ''}`} />
                        </button>
                    </div>
                </div>
            </div>

            {/* List */}
            {loading ? (
                <div className="flex items-center justify-center py-16 text-slate-400">
                    <FaIcon className="fas fa-spinner fa-spin mr-2" /> Загрузка…
                </div>
            ) : filtered.length === 0 ? (
                <div className={`${iosCard} flex flex-col items-center justify-center py-16 text-slate-400`}>
                    <FaIcon className="fas fa-layer-group mb-2" style={{ width: 28, height: 28 }} />
                    <p className="text-[13px]">{search ? 'Ничего не найдено' : 'Нет отделов'}</p>
                </div>
            ) : (
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                    {filtered.map((dept) => (
                        <div key={dept.id} className={`${iosCard} p-4`}>
                            <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="flex items-center gap-2">
                                        <span className="rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[11.5px] text-slate-500">{dept.code}</span>
                                        {dept.is_active === false
                                            ? <IosBadge tone="red">Отключён</IosBadge>
                                            : <IosBadge tone="green">Активен</IosBadge>}
                                    </div>
                                    <h3 className="mt-1.5 truncate text-[15px] font-semibold text-slate-900">{dept.name}</h3>
                                    {dept.description && <p className="mt-0.5 line-clamp-2 text-[12.5px] text-slate-500">{dept.description}</p>}
                                </div>
                                {isSuperAdmin && (
                                    <button onClick={() => openEdit(dept)} className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-blue-50 hover:text-blue-600" title="Редактировать">
                                        <FaIcon className="fas fa-pen" style={{ width: 13, height: 13 }} />
                                    </button>
                                )}
                            </div>

                            {/* Head row */}
                            <div className="mt-3 flex items-center justify-between gap-2 rounded-xl bg-slate-50 px-3 py-2.5">
                                <div className="flex min-w-0 items-center gap-2.5">
                                    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white text-slate-400 ring-1 ring-slate-200">
                                        <FaIcon className="fas fa-user-tie" style={{ width: 13, height: 13 }} />
                                    </div>
                                    <div className="min-w-0">
                                        <div className={iosGroupLabel}>Глава отдела</div>
                                        <div className="truncate text-[13.5px] font-medium text-slate-800">
                                            {dept.head_name || <span className="text-slate-400">Не назначена</span>}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex shrink-0 items-center gap-1">
                                    <button onClick={() => { setHeadDept(dept); setHeadQuery(''); }} className={iosBtnGhost} title="Назначить / сменить главу">
                                        <FaIcon className="fas fa-user-pen" />
                                        <span className="hidden sm:inline">{dept.head_user_id ? 'Сменить' : 'Назначить'}</span>
                                    </button>
                                    <button onClick={() => openHistory(dept)} className={iosBtnGhost} title="История смены главы">
                                        <FaIcon className="fas fa-clock-rotate-left" />
                                    </button>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* Create / edit modal */}
            <IosModal
                open={formOpen}
                onClose={closeForm}
                title={editingId ? 'Редактирование отдела' : 'Новый отдел'}
                subtitle={editingId ? form.name : 'Создание отдела'}
                footer={(
                    <>
                        <button type="button" onClick={closeForm} className={iosBtnSecondary}>Отмена</button>
                        <button type="button" onClick={submitForm} disabled={saving} className={iosBtnPrimary}>
                            <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                            {editingId ? 'Сохранить' : 'Создать'}
                        </button>
                    </>
                )}
            >
                <form onSubmit={submitForm} className="space-y-4">
                    <div>
                        <label className={iosGroupLabel}>Код</label>
                        <input type="text" value={form.code} placeholder="szov"
                            onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                            className={`${iosInput} mt-1`} />
                    </div>
                    <div>
                        <label className={iosGroupLabel}>Название</label>
                        <input type="text" value={form.name} placeholder="Служба заботы о водителях"
                            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                            className={`${iosInput} mt-1`} required />
                    </div>
                    <div>
                        <label className={iosGroupLabel}>Описание</label>
                        <textarea value={form.description} rows={3} placeholder="Необязательное описание"
                            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                            className={`${iosInput} mt-1 resize-none`} />
                    </div>
                    {editingId && (
                        <div className="flex items-center justify-between rounded-xl bg-slate-50 px-3.5 py-2.5">
                            <span className="text-[13.5px] font-medium text-slate-700">Активен</span>
                            <IosToggle checked={form.is_active} onChange={(v) => setForm((f) => ({ ...f, is_active: v }))} />
                        </div>
                    )}
                </form>
            </IosModal>

            {/* Head assignment modal */}
            <IosModal
                open={!!headDept}
                onClose={() => { setHeadDept(null); setHeadQuery(''); }}
                title="Глава отдела"
                subtitle={headDept?.name}
                footer={headDept?.head_user_id ? (
                    <button type="button" onClick={() => applyHead(null)} disabled={headSaving}
                        className="inline-flex items-center gap-2 rounded-xl bg-rose-50 px-4 py-2.5 text-[13.5px] font-semibold text-rose-600 transition hover:bg-rose-100 active:scale-[0.98] disabled:opacity-50">
                        <FaIcon className={headSaving ? 'fas fa-spinner fa-spin' : 'fas fa-user-slash'} />
                        Снять главу
                    </button>
                ) : null}
            >
                <div className="relative mb-3">
                    <FaIcon className="fas fa-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" style={{ width: 13, height: 13 }} />
                    <input type="text" placeholder="Поиск сотрудника…" value={headQuery}
                        onChange={(e) => setHeadQuery(e.target.value)} className={`${iosInput} pl-9`} />
                </div>
                <div className="space-y-1.5">
                    {headCandidates.length === 0 ? (
                        <p className="py-8 text-center text-[13px] text-slate-400">Сотрудники не найдены</p>
                    ) : headCandidates.map((u) => {
                        const isCurrent = Number(u.id) === Number(headDept?.head_user_id);
                        return (
                            <button key={u.id} type="button" disabled={headSaving || isCurrent}
                                onClick={() => applyHead(u.id)}
                                className={`flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-left transition ${
                                    isCurrent ? 'bg-blue-50 ring-1 ring-blue-200' : 'hover:bg-slate-50'
                                } disabled:cursor-default`}>
                                <div className="flex min-w-0 items-center gap-2.5">
                                    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 text-[12px] font-semibold">
                                        {(u.name || '?').trim().charAt(0).toUpperCase()}
                                    </div>
                                    <div className="min-w-0">
                                        <div className="truncate text-[13.5px] font-medium text-slate-800">{u.name}</div>
                                        <div className="text-[11.5px] text-slate-400">{roleLabel(u.role)}</div>
                                    </div>
                                </div>
                                {isCurrent
                                    ? <IosBadge tone="blue">Текущая</IosBadge>
                                    : <FaIcon className="fas fa-chevron-right text-slate-300" style={{ width: 12, height: 12 }} />}
                            </button>
                        );
                    })}
                </div>
            </IosModal>

            {/* Head history modal */}
            <IosModal
                open={!!historyDept}
                onClose={() => setHistoryDept(null)}
                title="История главы отдела"
                subtitle={historyDept?.name}
            >
                {historyLoading ? (
                    <div className="flex items-center justify-center py-10 text-slate-400">
                        <FaIcon className="fas fa-spinner fa-spin mr-2" /> Загрузка…
                    </div>
                ) : history.length === 0 ? (
                    <p className="py-10 text-center text-[13px] text-slate-400">Назначений ещё не было</p>
                ) : (
                    <ol className="relative space-y-3 border-l border-slate-200 pl-4">
                        {history.map((h) => (
                            <li key={h.id} className="relative">
                                <span className={`absolute -left-[21px] top-1 grid h-3.5 w-3.5 place-items-center rounded-full ring-4 ring-slate-50 ${
                                    h.action === 'assigned' ? 'bg-emerald-500' : 'bg-rose-400'
                                }`} />
                                <div className="flex items-center gap-2">
                                    <IosBadge tone={h.action === 'assigned' ? 'green' : 'red'}>
                                        {h.action === 'assigned' ? 'Назначена' : 'Снята'}
                                    </IosBadge>
                                    <span className="text-[13.5px] font-medium text-slate-800">{h.user_name || '—'}</span>
                                </div>
                                <div className="mt-0.5 text-[11.5px] text-slate-400">
                                    {fmtDate(h.created_at)}{h.changed_by_name ? ` · кем: ${h.changed_by_name}` : ''}
                                </div>
                            </li>
                        ))}
                    </ol>
                )}
            </IosModal>
        </div>
    );
};

export default DepartmentsView;
