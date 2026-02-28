import React, { useEffect, useMemo, useState } from 'react';
import FaIcon from '../common/FaIcon';

const HistoryModal = ({ isOpen, onClose, history = [], subjectName = "" }) => {
const [query, setQuery] = useState("");
const searchRef = React.useRef(null);

// Фокус на строку поиска при открытии
useEffect(() => {
    if (isOpen) {
    setTimeout(() => searchRef.current?.focus(), 50);
    }
}, [isOpen]);

// Закрытие по Escape
useEffect(() => {
    const onKey = (e) => {
    if (e.key === "Escape") onClose();
    };
    if (isOpen) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
}, [isOpen, onClose]);

const formatDate = (raw) => {
    if (!raw) return "-";
    const toDate = (v) => {
    const d = new Date(v);
    if (!isNaN(d)) return d;
    const parts = String(v).split(/[-/. ]/);
    if (parts.length === 3) {
        const [a, b, c] = parts;
        const maybe = new Date(`${c}-${b}-${a}`);
        if (!isNaN(maybe)) return maybe;
    }
    return null;
    };

    const d = toDate(raw);
    if (!d) return String(raw);

    const months = ['янв.', 'февр.', 'мар.', 'апр.', 'май', 'июн.', 'июл.', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'];
    const dd = String(d.getDate()).padStart(2, '0');
    const mon = months[d.getMonth()] || '';
    const yyyy = d.getFullYear();
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');

    return `${dd} ${mon} ${yyyy} ${hh}:${mm}`;
};

const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return history;
    return history.filter((e) =>
    [e.field, e.old_value, e.new_value, e.changed_by, e.changed_at]
        .map((v) => (v == null ? "" : String(v).toLowerCase()))
        .some((s) => s.includes(q))
    );
}, [history, query]);

if (!isOpen) return null;

return (
    <>
    {/* Backdrop */}
    <div
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-200"
        onClick={onClose}
        aria-hidden="true"
    />

    {/* Modal */}
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-modal-title"
        className="w-full max-w-4xl bg-white/95 dark:bg-slate-900/95 rounded-2xl shadow-2xl overflow-hidden transform transition-all duration-200"
        onClick={(e) => e.stopPropagation()}
        >
        <div className="px-6 py-4">
            {/* Header: иконка + заголовок и перенос строки с именем */}
            <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
                <FaIcon className="fas fa-history text-xl text-blue-700 mt-1" aria-hidden="true" />
                <div className="flex flex-col">
                <span id="history-modal-title" className="text-xl font-semibold text-blue-800 dark:text-blue-100">
                    История
                </span>
                {subjectName && (
                    <span className="text-xl text-gray-600 dark:text-gray-400 mt-1">
                    {subjectName}
                    </span>
                )}
                </div>
            </div>

            <div className="flex items-center gap-3">
                <input
                ref={searchRef}
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Поиск по полю, значению, пользователю или дате..."
                className="px-3 py-2 border border-gray-300 rounded-lg w-80 focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                aria-label="Поиск в истории"
                />
                <button
                onClick={onClose}
                className="px-3 py-2 bg-gray-200 text-gray-800 rounded-lg hover:bg-gray-300 transition"
                aria-label="Закрыть"
                >
                Закрыть
                </button>
            </div>
            </div>

            {/* Content */}
            <div className="mt-4 max-h-[60vh] overflow-auto rounded-lg border border-gray-200 bg-white">
            {filtered && filtered.length > 0 ? (
                <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50 sticky top-0 z-10">
                    <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Поле</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Старое значение</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Новое значение</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Кто изменил</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Дата</th>
                    </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                    {filtered.map((entry, i) => (
                    <tr key={i} className="group hover:bg-gray-50">
                        <td className="px-6 py-3 whitespace-nowrap align-top">{entry.field}</td>
                        <td className="px-6 py-3 whitespace-pre-line align-top text-sm text-gray-700">{entry.old_value ?? "-"}</td>
                        <td className="px-6 py-3 whitespace-pre-line align-top text-sm text-gray-700">{entry.new_value ?? "-"}</td>
                        <td className="px-6 py-3 whitespace-nowrap align-top">{entry.changed_by ?? "-"}</td>
                        <td className="px-6 py-3 whitespace-nowrap align-top">{formatDate(entry.changed_at)}</td>
                    </tr>
                    ))}
                </tbody>
                </table>
            ) : (
                <div className="p-6 text-center text-gray-600">История пуста.</div>
            )}
            </div>

            {/* Footer note */}
            <p className="mt-3 text-xs text-gray-400">Поиск фильтрует по всем колонкам. ESC или клик по фону — закрыть.</p>
        </div>
        </div>
    </div>
    </>
);
};

export default HistoryModal;
