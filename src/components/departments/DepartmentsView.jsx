import React, { useState, useEffect, useCallback, useRef } from 'react';
import FaIcon from '../common/FaIcon';

const EMPTY_FORM = { code: '', name: '', description: '' };

const DepartmentsView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader }) => {
    const [departments, setDepartments] = useState([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [editingId, setEditingId] = useState(null);
    const [form, setForm] = useState({ ...EMPTY_FORM });
    const [search, setSearch] = useState('');
    const formRef = useRef(null);

    const fetchDepartments = useCallback(async () => {
        setLoading(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/admin/departments`, {
                credentials: 'include',
                headers: withAccessTokenHeader({ 'X-User-Id': String(user.id) })
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.departments) {
                setDepartments(data.departments);
            } else {
                showToast?.(data.error || 'Не удалось загрузить отделы', 'error');
            }
        } catch (err) {
            showToast?.('Ошибка сети при загрузке отделов', 'error');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, user?.id, withAccessTokenHeader, showToast]);

    useEffect(() => {
        fetchDepartments();
    }, [fetchDepartments]);

    const handleCreate = async (e) => {
        e.preventDefault();
        if (!form.code.trim() || !form.name.trim()) {
            showToast?.('Код и название обязательны', 'error');
            return;
        }
        setSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/admin/departments`, {
                method: 'POST',
                credentials: 'include',
                headers: withAccessTokenHeader({
                    'Content-Type': 'application/json',
                    'X-User-Id': String(user.id)
                }),
                body: JSON.stringify({
                    code: form.code.trim(),
                    name: form.name.trim(),
                    description: form.description.trim() || null
                })
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.department) {
                setDepartments((prev) => [...prev, data.department]);
                setForm({ ...EMPTY_FORM });
                setShowCreateForm(false);
                showToast?.('Отдел создан', 'success');
            } else {
                showToast?.(data.error || 'Ошибка при создании', 'error');
            }
        } catch {
            showToast?.('Ошибка сети', 'error');
        } finally {
            setSaving(false);
        }
    };

    const startEdit = (dept) => {
        setEditingId(dept.id);
        setForm({ code: dept.code || '', name: dept.name || '', description: dept.description || '' });
        setShowCreateForm(false);
        setTimeout(() => formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
    };

    const cancelEdit = () => {
        setEditingId(null);
        setForm({ ...EMPTY_FORM });
    };

    const handleUpdate = async (e) => {
        e.preventDefault();
        if (!editingId) return;
        if (!form.name.trim()) {
            showToast?.('Название обязательно', 'error');
            return;
        }
        setSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/admin/departments/${editingId}`, {
                method: 'PUT',
                credentials: 'include',
                headers: withAccessTokenHeader({
                    'Content-Type': 'application/json',
                    'X-User-Id': String(user.id)
                }),
                body: JSON.stringify({
                    code: form.code.trim(),
                    slug: form.code.trim(),
                    name: form.name.trim(),
                    description: form.description.trim() || null
                })
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.department) {
                setDepartments((prev) => prev.map((d) => d.id === editingId ? data.department : d));
                cancelEdit();
                showToast?.('Отдел обновлен', 'success');
            } else {
                showToast?.(data.error || 'Ошибка при обновлении', 'error');
            }
        } catch {
            showToast?.('Ошибка сети', 'error');
        } finally {
            setSaving(false);
        }
    };

    const filtered = departments.filter((d) => {
        if (!search.trim()) return true;
        const q = search.trim().toLowerCase();
        return (d.name || '').toLowerCase().includes(q)
            || (d.code || '').toLowerCase().includes(q)
            || (d.description || '').toLowerCase().includes(q);
    });

    return (
        <div className="bg-white rounded-2xl shadow-sm mb-6 border border-gray-100 overflow-hidden">
            {/* Header */}
            <div className="px-5 py-4 border-b border-gray-100 bg-gray-50">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
                            <FaIcon className="fa-solid fa-layer-group text-blue-600" />
                            Отделы
                        </h2>
                        <p className="text-sm text-gray-500 mt-0.5">
                            Всего: {departments.length}
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <div className="relative">
                            <FaIcon className="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" style={{ width: 14, height: 14 }} />
                            <input
                                type="text"
                                placeholder="Поиск..."
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                className="pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent w-48"
                            />
                        </div>
                        <button
                            onClick={() => { setShowCreateForm((v) => !v); cancelEdit(); }}
                            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition"
                        >
                            <FaIcon className="fas fa-plus" />
                            <span className="hidden sm:inline">Новый отдел</span>
                        </button>
                        <button
                            onClick={fetchDepartments}
                            disabled={loading}
                            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition disabled:opacity-50"
                            title="Обновить"
                        >
                            <FaIcon className={`fas fa-sync-alt ${loading ? 'animate-spin' : ''}`} />
                        </button>
                    </div>
                </div>
            </div>

            {/* Create form */}
            {showCreateForm && (
                <div className="px-5 py-4 bg-blue-50 border-b border-blue-100">
                    <form onSubmit={handleCreate} className="flex flex-col sm:flex-row gap-3 items-end">
                        <div className="flex-1 min-w-0">
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Код</label>
                            <input
                                type="text"
                                value={form.code}
                                onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                                placeholder="szov"
                                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                required
                            />
                        </div>
                        <div className="flex-[2] min-w-0">
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Название</label>
                            <input
                                type="text"
                                value={form.name}
                                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                                placeholder="Служба заботы о водителях"
                                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                required
                            />
                        </div>
                        <div className="flex-[2] min-w-0">
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Описание</label>
                            <input
                                type="text"
                                value={form.description}
                                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                                placeholder="Необязательное описание"
                                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                            />
                        </div>
                        <div className="flex gap-2 shrink-0">
                            <button
                                type="submit"
                                disabled={saving}
                                className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition disabled:opacity-50"
                            >
                                <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                                Создать
                            </button>
                            <button
                                type="button"
                                onClick={() => { setShowCreateForm(false); setForm({ ...EMPTY_FORM }); }}
                                className="px-3 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition"
                            >
                                Отмена
                            </button>
                        </div>
                    </form>
                </div>
            )}

            {/* Table */}
            <div className="overflow-x-auto">
                {loading ? (
                    <div className="flex items-center justify-center py-16 text-gray-400">
                        <FaIcon className="fas fa-spinner fa-spin mr-2" /> Загрузка...
                    </div>
                ) : filtered.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-16 text-gray-400">
                        <FaIcon className="fas fa-layer-group mb-2" style={{ width: 32, height: 32 }} />
                        <p className="text-sm">{search ? 'Ничего не найдено' : 'Нет отделов'}</p>
                    </div>
                ) : (
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Код</th>
                                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Название</th>
                                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider hidden md:table-cell">Описание</th>
                                <th className="px-5 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Действия</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-100">
                            {filtered.map((dept) => (
                                <React.Fragment key={dept.id}>
                                    <tr className={`hover:bg-gray-50 transition-colors ${editingId === dept.id ? 'bg-amber-50' : ''}`}>
                                        <td className="px-5 py-3 text-sm font-mono text-gray-700 whitespace-nowrap">{dept.code}</td>
                                        <td className="px-5 py-3 text-sm font-medium text-gray-900">{dept.name}</td>
                                        <td className="px-5 py-3 text-sm text-gray-500 hidden md:table-cell max-w-xs truncate">{dept.description || '—'}</td>
                                        <td className="px-5 py-3 text-right whitespace-nowrap">
                                            <div className="inline-flex items-center gap-1">
                                                <button
                                                    onClick={() => editingId === dept.id ? cancelEdit() : startEdit(dept)}
                                                    className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-md transition"
                                                    title="Редактировать"
                                                >
                                                    <FaIcon className="fas fa-pen" style={{ width: 14, height: 14 }} />
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                    {editingId === dept.id && (
                                        <tr ref={formRef}>
                                            <td colSpan={4} className="px-5 py-3 bg-amber-50 border-t border-amber-100">
                                                <form onSubmit={handleUpdate} className="flex flex-col sm:flex-row gap-3 items-end">
                                                    <div className="flex-1 min-w-0">
                                                        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Код</label>
                                                        <input
                                                            type="text"
                                                            value={form.code}
                                                            onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                                                            className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                                        />
                                                    </div>
                                                    <div className="flex-[2] min-w-0">
                                                        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Название</label>
                                                        <input
                                                            type="text"
                                                            value={form.name}
                                                            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                                                            className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                                            required
                                                        />
                                                    </div>
                                                    <div className="flex-[2] min-w-0">
                                                        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Описание</label>
                                                        <input
                                                            type="text"
                                                            value={form.description}
                                                            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                                                            className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                                        />
                                                    </div>
                                                    <div className="flex gap-2 shrink-0">
                                                        <button
                                                            type="submit"
                                                            disabled={saving}
                                                            className="inline-flex items-center gap-1.5 px-4 py-2 bg-amber-500 text-white text-sm font-medium rounded-lg hover:bg-amber-600 transition disabled:opacity-50"
                                                        >
                                                            <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-save'} />
                                                            Сохранить
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={cancelEdit}
                                                            className="px-3 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition"
                                                        >
                                                            Отмена
                                                        </button>
                                                    </div>
                                                </form>
                                            </td>
                                        </tr>
                                    )}
                                </React.Fragment>
                            ))}
                        </tbody>
                        <tfoot className="bg-gray-50">
                            <tr>
                                <td colSpan={4} className="px-5 py-3 text-sm text-gray-500">
                                    {filtered.length} {filtered.length === 1 ? 'отдел' : 'отделов'}
                                    {search && ` по запросу "${search}"`}
                                </td>
                            </tr>
                        </tfoot>
                    </table>
                )}
            </div>
        </div>
    );
};

export default DepartmentsView;
