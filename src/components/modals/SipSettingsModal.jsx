import React, { useEffect, useState, useCallback } from 'react';
import FaIcon from '../common/FaIcon';

/*
 * SipSettingsModal — панель «Настройки SIP» для iCORE Phone.
 *
 * Общие настройки телефонии: SIP-сервер/домен + база пароля. Полный пароль
 * оператора собирается на бэкенде как base_password + его sip_number.
 * Доступ (см. App.jsx canAccessSipSettings): админ / глава отдела / СВ отдела продаж.
 *
 * Родитель передаёт apiBase и getAuthHeaders() — авторизацией владеет App.jsx,
 * как и у остальных модалов.
 */
const SipSettingsModal = ({ isOpen, onClose, apiBase, getAuthHeaders, canEdit = true }) => {
    const [activeTab, setActiveTab] = useState('settings');
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    const [sipServer, setSipServer] = useState('');
    const [basePassword, setBasePassword] = useState('');
    const [showPassword, setShowPassword] = useState(false);
    const [meta, setMeta] = useState({ updated_by_name: null, updated_at: null });

    const [history, setHistory] = useState([]);
    const [historyLoading, setHistoryLoading] = useState(false);

    const authHeaders = useCallback(
        (extra = {}) => (typeof getAuthHeaders === 'function' ? getAuthHeaders(extra) : { 'Content-Type': 'application/json', ...extra }),
        [getAuthHeaders]
    );

    const loadConfig = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const res = await fetch(`${apiBase}/api/sip_config`, {
                method: 'GET',
                credentials: 'include',
                headers: authHeaders(),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
            const s = data.settings || {};
            setSipServer(s.sip_server || '');
            setBasePassword(s.base_password || '');
            setMeta({ updated_by_name: s.updated_by_name || null, updated_at: s.updated_at || null });
        } catch (e) {
            setError(`Не удалось загрузить настройки: ${e.message}`);
        } finally {
            setLoading(false);
        }
    }, [apiBase, authHeaders]);

    const loadHistory = useCallback(async () => {
        setHistoryLoading(true);
        try {
            const res = await fetch(`${apiBase}/api/sip_config/history?limit=100`, {
                method: 'GET',
                credentials: 'include',
                headers: authHeaders(),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
            setHistory(Array.isArray(data.history) ? data.history : []);
        } catch (e) {
            setHistory([]);
        } finally {
            setHistoryLoading(false);
        }
    }, [apiBase, authHeaders]);

    useEffect(() => {
        if (isOpen) {
            setActiveTab('settings');
            setSuccess('');
            setError('');
            setShowPassword(false);
            loadConfig();
            loadHistory();
        }
    }, [isOpen, loadConfig, loadHistory]);

    const handleSave = async () => {
        if (!canEdit) return;
        setSaving(true);
        setError('');
        setSuccess('');
        try {
            const res = await fetch(`${apiBase}/api/sip_config`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders(),
                body: JSON.stringify({ sip_server: sipServer.trim(), base_password: basePassword }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
            const s = data.settings || {};
            setMeta({ updated_by_name: s.updated_by_name || null, updated_at: s.updated_at || null });
            setSuccess('Настройки сохранены');
            loadHistory();
        } catch (e) {
            setError(`Не удалось сохранить: ${e.message}`);
        } finally {
            setSaving(false);
        }
    };

    const fmtDate = (iso) => {
        if (!iso) return '—';
        try { return new Date(iso).toLocaleString('ru-RU'); } catch { return iso; }
    };

    if (!isOpen) return null;

    const tabs = [
        { id: 'settings', label: 'Настройки' },
        { id: 'history', label: 'История' },
    ];

    const inputCls =
        'w-full rounded-lg border border-gray-300 dark:border-slate-700 bg-white dark:bg-slate-800 ' +
        'px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/60 transition';

    return (
        <>
            <div
                className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-300"
                onClick={onClose}
                aria-hidden="true"
            />
            <div
                className="fixed inset-0 z-50 flex items-center justify-center p-4"
                tabIndex={-1}
                onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
            >
                <div
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="sip-settings-title"
                    className="pointer-events-auto w-full max-w-lg bg-white/95 dark:bg-slate-900/95 rounded-2xl shadow-2xl overflow-hidden transform transition-all duration-300 animate-scale-in"
                    onClick={(e) => e.stopPropagation()}
                >
                    <div className="px-6 py-5 max-h-[88vh] overflow-y-auto">
                        {/* Header */}
                        <div className="flex items-start justify-between gap-4">
                            <h2 id="sip-settings-title" className="text-2xl font-bold text-gray-800 dark:text-gray-100 flex items-center gap-2">
                                <FaIcon className="fas fa-headset text-blue-600"></FaIcon>
                                Настройки SIP
                            </h2>
                            <button
                                type="button"
                                onClick={onClose}
                                aria-label="Закрыть"
                                className="rounded-md p-2 text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-slate-800 transition"
                            >
                                <FaIcon className="fas fa-times text-lg" />
                            </button>
                        </div>

                        {/* Tabs */}
                        <div className="mt-4 grid grid-cols-2 gap-2 rounded-xl bg-gray-100 dark:bg-slate-800 p-1">
                            {tabs.map((tab) => (
                                <button
                                    key={tab.id}
                                    type="button"
                                    onClick={() => setActiveTab(tab.id)}
                                    className={`px-3 py-2 rounded-lg text-sm font-medium transition ${
                                        activeTab === tab.id
                                            ? 'bg-white dark:bg-slate-900 text-blue-700 dark:text-blue-300 shadow-sm'
                                            : 'text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white'
                                    }`}
                                    aria-pressed={activeTab === tab.id}
                                >
                                    {tab.label}
                                </button>
                            ))}
                        </div>

                        {(error || success) && (
                            <div className={`mt-4 rounded-lg px-3 py-2 text-sm ${error ? 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300' : 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300'}`}>
                                {error || success}
                            </div>
                        )}

                        {/* Settings tab */}
                        {activeTab === 'settings' && (
                            <div className="mt-5 space-y-5">
                                {loading ? (
                                    <div className="py-10 text-center text-gray-500 dark:text-gray-400">
                                        <FaIcon className="fas fa-spinner fa-spin mr-2" /> Загрузка…
                                    </div>
                                ) : (
                                    <>
                                        <div>
                                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">SIP-сервер / домен</label>
                                            <input
                                                type="text"
                                                value={sipServer}
                                                onChange={(e) => setSipServer(e.target.value)}
                                                placeholder="напр. 192.168.88.251"
                                                disabled={!canEdit}
                                                className={inputCls}
                                            />
                                        </div>
                                        <div>
                                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">База пароля</label>
                                            <div className="relative">
                                                <input
                                                    type={showPassword ? 'text' : 'password'}
                                                    value={basePassword}
                                                    onChange={(e) => setBasePassword(e.target.value)}
                                                    placeholder="общая часть пароля"
                                                    disabled={!canEdit}
                                                    autoComplete="new-password"
                                                    className={inputCls + ' pr-10'}
                                                />
                                                <button
                                                    type="button"
                                                    onClick={() => setShowPassword((v) => !v)}
                                                    aria-label={showPassword ? 'Скрыть' : 'Показать'}
                                                    className="absolute inset-y-0 right-0 px-3 text-gray-500 hover:text-gray-700 dark:hover:text-gray-200"
                                                >
                                                    <FaIcon className={`fas ${showPassword ? 'fa-eye-slash' : 'fa-eye'}`} />
                                                </button>
                                            </div>
                                            <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
                                                Полный пароль оператора = <span className="font-medium">база + его SIP-номер</span>. SIP-номер задаётся в карточке сотрудника.
                                            </p>
                                        </div>

                                        <div className="text-xs text-gray-400 dark:text-gray-500">
                                            {meta.updated_at ? (
                                                <>Изменено: {fmtDate(meta.updated_at)}{meta.updated_by_name ? ` · ${meta.updated_by_name}` : ''}</>
                                            ) : 'Ещё не настраивалось'}
                                        </div>

                                        {canEdit && (
                                            <div className="flex justify-end pt-1">
                                                <button
                                                    type="button"
                                                    onClick={handleSave}
                                                    disabled={saving}
                                                    className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-60 transition"
                                                >
                                                    {saving ? <FaIcon className="fas fa-spinner fa-spin" /> : <FaIcon className="fas fa-check" />}
                                                    Сохранить
                                                </button>
                                            </div>
                                        )}
                                    </>
                                )}
                            </div>
                        )}

                        {/* History tab */}
                        {activeTab === 'history' && (
                            <div className="mt-5">
                                {historyLoading ? (
                                    <div className="py-10 text-center text-gray-500 dark:text-gray-400">
                                        <FaIcon className="fas fa-spinner fa-spin mr-2" /> Загрузка…
                                    </div>
                                ) : history.length === 0 ? (
                                    <div className="py-10 text-center text-gray-400 dark:text-gray-500">Изменений пока нет</div>
                                ) : (
                                    <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-slate-700">
                                        <table className="min-w-full text-sm">
                                            <thead className="bg-gray-50 dark:bg-slate-800 text-gray-600 dark:text-gray-300">
                                                <tr>
                                                    <th className="px-3 py-2 text-left font-medium">Дата</th>
                                                    <th className="px-3 py-2 text-left font-medium">Кто менял</th>
                                                    <th className="px-3 py-2 text-left font-medium">Сервер</th>
                                                    <th className="px-3 py-2 text-left font-medium">Пароль</th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-gray-100 dark:divide-slate-800">
                                                {history.map((h, i) => (
                                                    <tr key={i} className="text-gray-800 dark:text-gray-200">
                                                        <td className="px-3 py-2 whitespace-nowrap">{fmtDate(h.changed_at)}</td>
                                                        <td className="px-3 py-2">{h.changed_by_name || '—'}</td>
                                                        <td className="px-3 py-2">{(h.settings && h.settings.sip_server) || '—'}</td>
                                                        <td className="px-3 py-2 font-mono text-xs">{(h.settings && h.settings.base_password) || '—'}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </>
    );
};

export default SipSettingsModal;
