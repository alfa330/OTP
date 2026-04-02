import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import { normalizeRole, isAdminLikeRole, isSupervisorRole } from '../../utils/roles';

const FALLBACK_TECHNICAL_REASONS = [
    'Не работает интернет',
    'Замена мыши',
    'Не работает микрофон',
    'Не работает Oktell',
    'Проблема с маршрутизацией Oktell (не идут исходящие звонки), переключение в ручной режим',
    'Замена клавиатуры',
    'Не заходит в корпоративный чат',
    'Не включается компьютер',
    'Переполнена память',
    'Кнопка "Войти в колл-центр" в Oktell не реагирует на действия',
    'Виснет компьютер',
    'Не работают программы на ПК (ошибка "Меню "Пуск" не работает")',
    'Проблема с подключением к сайту Oktell',
    'Не может войти в учетную запись ПК',
    'Не поступают звонки',
    'Не может войти в учетную запись Oktell',
    'Отключение света',
    'Массовая проблема с Октелл',
    'Массовая проблема с интернетом',
    'Массовая проблема с телефонией'
];

const toIsoDate = (value = new Date()) => {
    try {
        return new Date(value).toISOString().slice(0, 10);
    } catch (e) {
        return '';
    }
};

const currentMonthStartIso = () => {
    const now = new Date();
    return toIsoDate(new Date(now.getFullYear(), now.getMonth(), 1));
};

const toIntList = (values) => {
    const list = Array.isArray(values) ? values : [values];
    const out = [];
    const seen = new Set();
    for (const item of list) {
        const n = Number(item);
        if (!Number.isFinite(n)) continue;
        const id = Math.trunc(n);
        if (id <= 0 || seen.has(id)) continue;
        seen.add(id);
        out.push(id);
    }
    return out;
};

const readMultiSelectIntValues = (event) =>
    toIntList(Array.from(event?.target?.selectedOptions || []).map((option) => option.value));

const TechnicalIssuesView = ({
    user,
    operators = [],
    directions = [],
    showToast,
    apiBaseUrl,
    withAccessTokenHeader
}) => {
    const role = normalizeRole(user?.role);
    const canCreate = isAdminLikeRole(role) || isSupervisorRole(role);
    const canView = isAdminLikeRole(role) || isSupervisorRole(role);
    const canExport = isAdminLikeRole(role);
    const canDelete = isAdminLikeRole(role) || isSupervisorRole(role);

    const notify = useCallback(
        (message, type = 'info') => {
            if (typeof showToast === 'function') {
                showToast(message, type);
            }
        },
        [showToast]
    );

    const buildHeaders = useCallback(() => {
        const baseHeaders = {
            'X-API-Key': user?.apiKey || '',
            'X-User-Id': user?.id || ''
        };
        const headers = {};
        Object.entries(baseHeaders).forEach(([key, value]) => {
            if (value !== undefined && value !== null && String(value).trim() !== '') {
                headers[key] = value;
            }
        });
        if (typeof withAccessTokenHeader === 'function') {
            return withAccessTokenHeader(headers);
        }
        return headers;
    }, [user?.apiKey, user?.id, withAccessTokenHeader]);

    const visibleOperators = useMemo(() => {
        const list = Array.isArray(operators) ? operators : [];
        const filtered = list.filter((op) => {
            const opRole = String(op?.role || 'operator').trim().toLowerCase();
            if (opRole !== 'operator') return false;
            return true;
        });
        return filtered.sort((a, b) =>
            String(a?.name || '').localeCompare(String(b?.name || ''), 'ru', { sensitivity: 'base' })
        );
    }, [operators]);

    const visibleDirections = useMemo(() => {
        const list = Array.isArray(directions) ? directions : [];
        const allowedDirectionIds = new Set(
            visibleOperators
                .map((op) => Number(op?.direction_id))
                .filter((id) => Number.isFinite(id) && id > 0)
        );
        const result = list.filter((direction) => {
            const directionId = Number(direction?.id);
            if (!Number.isFinite(directionId) || directionId <= 0) return false;
            if (isAdminLikeRole(role) || isSupervisorRole(role)) return true;
            return allowedDirectionIds.has(directionId);
        });
        return result.sort((a, b) =>
            String(a?.name || '').localeCompare(String(b?.name || ''), 'ru', { sensitivity: 'base' })
        );
    }, [directions, role, visibleOperators]);

    const [reasons, setReasons] = useState(FALLBACK_TECHNICAL_REASONS);
    const [rows, setRows] = useState([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [deletingIssueId, setDeletingIssueId] = useState(null);

    const [createDate, setCreateDate] = useState(() => toIsoDate(new Date()));
    const [createStartTime, setCreateStartTime] = useState('00:00');
    const [createEndTime, setCreateEndTime] = useState('23:59');
    const [createReason, setCreateReason] = useState('');
    const [createComment, setCreateComment] = useState('');
    const [createOperatorIds, setCreateOperatorIds] = useState([]);
    const [createDirectionIds, setCreateDirectionIds] = useState([]);

    const [filterDateFrom, setFilterDateFrom] = useState(() => currentMonthStartIso());
    const [filterDateTo, setFilterDateTo] = useState(() => toIsoDate(new Date()));
    const [filterOperatorId, setFilterOperatorId] = useState('');
    const [filterReason, setFilterReason] = useState('');

    const fetchReasons = useCallback(async () => {
        if (!canView) return;
        try {
            const response = await axios.get(`${apiBaseUrl}/api/technical_issues/reasons`, {
                headers: buildHeaders()
            });
            const nextReasons = Array.isArray(response?.data?.reasons) ? response.data.reasons : [];
            if (nextReasons.length > 0) {
                setReasons(nextReasons);
            }
        } catch (error) {
            setReasons(FALLBACK_TECHNICAL_REASONS);
        }
    }, [apiBaseUrl, buildHeaders, canView]);

    const fetchRows = useCallback(
        async (override = {}) => {
            if (!canView) return;

            const dateFrom = Object.prototype.hasOwnProperty.call(override, 'dateFrom')
                ? override.dateFrom
                : filterDateFrom;
            const dateTo = Object.prototype.hasOwnProperty.call(override, 'dateTo')
                ? override.dateTo
                : filterDateTo;
            const operatorId = Object.prototype.hasOwnProperty.call(override, 'operatorId')
                ? override.operatorId
                : filterOperatorId;
            const reason = Object.prototype.hasOwnProperty.call(override, 'reason')
                ? override.reason
                : filterReason;

            const query = new URLSearchParams();
            query.set('limit', '1000');
            if (dateFrom) query.set('date_from', dateFrom);
            if (dateTo) query.set('date_to', dateTo);
            if (operatorId) query.set('operator_id', String(operatorId));
            if (reason) query.set('reason', reason);

            setLoading(true);
            try {
                const response = await axios.get(`${apiBaseUrl}/api/technical_issues?${query.toString()}`, {
                    headers: buildHeaders()
                });
                const items = Array.isArray(response?.data?.items) ? response.data.items : [];
                const nextTotal = Number(response?.data?.total || items.length || 0);
                setRows(items);
                setTotal(nextTotal);

                const nextReasons = Array.isArray(response?.data?.reasons) ? response.data.reasons : [];
                if (nextReasons.length > 0) {
                    setReasons(nextReasons);
                }
            } catch (error) {
                const message = error?.response?.data?.error || 'Не удалось загрузить список технических причин';
                notify(message, 'error');
            } finally {
                setLoading(false);
            }
        },
        [apiBaseUrl, buildHeaders, canView, filterDateFrom, filterDateTo, filterOperatorId, filterReason, notify]
    );

    useEffect(() => {
        if (!canView) return;
        fetchReasons();
        fetchRows();
    }, [canView, fetchReasons, fetchRows]);

    const handleCreateIssue = async (event) => {
        event.preventDefault();
        if (!canCreate) return;

        if (!createDate) {
            notify('Укажите дату технической причины', 'error');
            return;
        }
        if (!createStartTime || !createEndTime) {
            notify('Укажите время начала и окончания', 'error');
            return;
        }
        if (createStartTime === createEndTime) {
            notify('Время начала и окончания не должно совпадать', 'error');
            return;
        }
        if (!createReason) {
            notify('Выберите техническую причину', 'error');
            return;
        }
        if (createOperatorIds.length === 0 && createDirectionIds.length === 0) {
            notify('Выберите операторов или направления', 'error');
            return;
        }

        setSubmitting(true);
        try {
            const payload = {
                date: createDate,
                start_time: createStartTime,
                end_time: createEndTime,
                reason: createReason,
                comment: createComment || null,
                operator_ids: toIntList(createOperatorIds),
                direction_ids: toIntList(createDirectionIds)
            };
            const response = await axios.post(`${apiBaseUrl}/api/technical_issues`, payload, {
                headers: buildHeaders()
            });
            const createdCount = Number(response?.data?.result?.created_count || 0);
            notify(
                createdCount > 0
                    ? `Сохранено записей: ${createdCount}`
                    : 'Техническая причина сохранена',
                'success'
            );
            setCreateComment('');
            await fetchRows();
        } catch (error) {
            const message = error?.response?.data?.error || 'Не удалось сохранить техническую причину';
            notify(message, 'error');
        } finally {
            setSubmitting(false);
        }
    };

    const handleApplyFilters = async () => {
        await fetchRows();
    };

    const handleResetFilters = async () => {
        const nextFrom = currentMonthStartIso();
        const nextTo = toIsoDate(new Date());
        setFilterDateFrom(nextFrom);
        setFilterDateTo(nextTo);
        setFilterOperatorId('');
        setFilterReason('');
        await fetchRows({
            dateFrom: nextFrom,
            dateTo: nextTo,
            operatorId: '',
            reason: ''
        });
    };

    const handleExport = async () => {
        if (!canExport) return;
        setExporting(true);
        try {
            const query = new URLSearchParams();
            if (filterDateFrom) query.set('date_from', filterDateFrom);
            if (filterDateTo) query.set('date_to', filterDateTo);
            if (filterOperatorId) query.set('operator_id', String(filterOperatorId));
            if (filterReason) query.set('reason', filterReason);

            const response = await axios.get(
                `${apiBaseUrl}/api/technical_issues/export_excel?${query.toString()}`,
                {
                    headers: buildHeaders(),
                    responseType: 'blob'
                }
            );

            const blob = new Blob([response.data], {
                type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            });
            const linkUrl = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = linkUrl;
            a.download = `technical_issues_${toIsoDate(new Date())}.xlsx`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(linkUrl);
        } catch (error) {
            const message = error?.response?.data?.error || 'Не удалось выгрузить Excel';
            notify(message, 'error');
        } finally {
            setExporting(false);
        }
    };

    const handleDeleteIssue = async (issue) => {
        if (!canDelete) return;
        const issueId = Number(issue?.id);
        if (!Number.isFinite(issueId) || issueId <= 0) return;

        const operatorName = String(issue?.operator_name || '').trim();
        const issueDate = String(issue?.date || '').trim();
        const confirmMessage = operatorName
            ? `Удалить техсбой оператора "${operatorName}"${issueDate ? ` (${issueDate})` : ''}?`
            : `Удалить техсбой${issueDate ? ` (${issueDate})` : ''}?`;
        if (typeof window !== 'undefined' && !window.confirm(confirmMessage)) return;

        setDeletingIssueId(issueId);
        try {
            await axios.delete(`${apiBaseUrl}/api/technical_issues/${issueId}`, {
                headers: buildHeaders()
            });
            notify('Техсбой удален', 'success');
            await fetchRows();
        } catch (error) {
            const message = error?.response?.data?.error || 'Не удалось удалить техсбой';
            notify(message, 'error');
        } finally {
            setDeletingIssueId(null);
        }
    };

    if (!canView) {
        return (
            <div className="bg-white p-6 rounded-xl shadow border border-gray-200">
                <div className="text-sm text-gray-600">Раздел доступен только администраторам и супервайзерам.</div>
            </div>
        );
    }

    return (
        <div className="space-y-5">
            <div className="bg-white p-5 rounded-xl shadow border border-gray-200">
                <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
                    <FaIcon className="fas fa-tools text-blue-600" />
                    Технические причины
                </h2>
                <p className="mt-1 text-sm text-gray-600">
                    Фиксация технических проблем операторов, фильтрация и выгрузка журнала.
                </p>
            </div>

            {canCreate && (
                <form
                    onSubmit={handleCreateIssue}
                    className="bg-white p-5 rounded-xl shadow border border-gray-200 space-y-4"
                >
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <label className="block">
                            <span className="text-sm font-medium text-gray-700">Дата проблемы</span>
                            <input
                                type="date"
                                value={createDate}
                                onChange={(event) => setCreateDate(event.target.value)}
                                className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                                required
                            />
                        </label>
                        <label className="block">
                            <span className="text-sm font-medium text-gray-700">Время начала</span>
                            <input
                                type="time"
                                value={createStartTime}
                                onChange={(event) => setCreateStartTime(event.target.value)}
                                className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                                required
                            />
                        </label>
                        <label className="block">
                            <span className="text-sm font-medium text-gray-700">Время окончания</span>
                            <input
                                type="time"
                                value={createEndTime}
                                onChange={(event) => setCreateEndTime(event.target.value)}
                                className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                                required
                            />
                        </label>
                        <label className="block">
                            <span className="text-sm font-medium text-gray-700">Техническая причина</span>
                            <select
                                value={createReason}
                                onChange={(event) => setCreateReason(event.target.value)}
                                className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                                required
                            >
                                <option value="">Выберите причину</option>
                                {reasons.map((reason) => (
                                    <option key={reason} value={reason}>
                                        {reason}
                                    </option>
                                ))}
                            </select>
                        </label>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <label className="block">
                            <span className="text-sm font-medium text-gray-700">Операторы (мультивыбор)</span>
                            <select
                                multiple
                                value={createOperatorIds.map(String)}
                                onChange={(event) => setCreateOperatorIds(readMultiSelectIntValues(event))}
                                className="mt-1 w-full min-h-[140px] rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                                {visibleOperators.map((operator) => (
                                    <option key={operator.id} value={operator.id}>
                                        {operator.name}
                                    </option>
                                ))}
                            </select>
                            <span className="mt-1 block text-xs text-gray-500">
                                Можно выбрать сотрудников вручную или использовать направления.
                            </span>
                        </label>

                        <label className="block">
                            <span className="text-sm font-medium text-gray-700">Направления (мультивыбор)</span>
                            <select
                                multiple
                                value={createDirectionIds.map(String)}
                                onChange={(event) => setCreateDirectionIds(readMultiSelectIntValues(event))}
                                className="mt-1 w-full min-h-[140px] rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                                {visibleDirections.map((direction) => (
                                    <option key={direction.id} value={direction.id}>
                                        {direction.name}
                                    </option>
                                ))}
                            </select>
                        </label>
                    </div>

                    <label className="block">
                        <span className="text-sm font-medium text-gray-700">Комментарий (необязательно)</span>
                        <textarea
                            value={createComment}
                            onChange={(event) => setCreateComment(event.target.value)}
                            rows={3}
                            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            placeholder="Дополнительное описание проблемы"
                        />
                    </label>

                    <div className="flex justify-end">
                        <button
                            type="submit"
                            disabled={submitting}
                            className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white ${
                                submitting ? 'bg-blue-400 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700'
                            }`}
                        >
                            <FaIcon className={`fas ${submitting ? 'fa-spinner fa-spin' : 'fa-save'}`} />
                            {submitting ? 'Сохранение...' : 'Сохранить причину'}
                        </button>
                    </div>
                </form>
            )}

            <div className="bg-white p-5 rounded-xl shadow border border-gray-200 space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                    <label className="block">
                        <span className="text-sm font-medium text-gray-700">Дата с</span>
                        <input
                            type="date"
                            value={filterDateFrom}
                            onChange={(event) => setFilterDateFrom(event.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                    </label>
                    <label className="block">
                        <span className="text-sm font-medium text-gray-700">Дата по</span>
                        <input
                            type="date"
                            value={filterDateTo}
                            onChange={(event) => setFilterDateTo(event.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                    </label>
                    <label className="block">
                        <span className="text-sm font-medium text-gray-700">Оператор</span>
                        <select
                            value={filterOperatorId}
                            onChange={(event) => setFilterOperatorId(event.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                            <option value="">Все операторы</option>
                            {visibleOperators.map((operator) => (
                                <option key={operator.id} value={operator.id}>
                                    {operator.name}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="block">
                        <span className="text-sm font-medium text-gray-700">Причина</span>
                        <select
                            value={filterReason}
                            onChange={(event) => setFilterReason(event.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                            <option value="">Все причины</option>
                            {reasons.map((reason) => (
                                <option key={reason} value={reason}>
                                    {reason}
                                </option>
                            ))}
                        </select>
                    </label>
                </div>

                <div className="flex flex-wrap gap-2">
                    <button
                        type="button"
                        onClick={handleApplyFilters}
                        className="inline-flex items-center gap-2 rounded-lg bg-slate-700 hover:bg-slate-800 px-4 py-2 text-sm text-white"
                    >
                        <FaIcon className="fas fa-filter" />
                        Применить фильтры
                    </button>
                    <button
                        type="button"
                        onClick={handleResetFilters}
                        className="inline-flex items-center gap-2 rounded-lg bg-gray-200 hover:bg-gray-300 px-4 py-2 text-sm text-gray-800"
                    >
                        <FaIcon className="fas fa-undo" />
                        Сбросить
                    </button>
                    {canExport && (
                        <button
                            type="button"
                            onClick={handleExport}
                            disabled={exporting}
                            className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm text-white ${
                                exporting ? 'bg-emerald-400 cursor-not-allowed' : 'bg-emerald-600 hover:bg-emerald-700'
                            }`}
                        >
                            <FaIcon className={`fas ${exporting ? 'fa-spinner fa-spin' : 'fa-file-excel'}`} />
                            {exporting ? 'Выгрузка...' : 'Выгрузить Excel'}
                        </button>
                    )}
                </div>
            </div>

            <div className="bg-white rounded-xl shadow border border-gray-200 overflow-hidden">
                <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between">
                    <div className="text-sm font-medium text-gray-800">Записи</div>
                    <div className="text-xs text-gray-500">Всего: {total}</div>
                </div>

                {loading ? (
                    <div className="p-6 text-sm text-gray-500 flex items-center gap-2">
                        <FaIcon className="fas fa-spinner fa-spin" />
                        Загрузка...
                    </div>
                ) : rows.length === 0 ? (
                    <div className="p-6 text-sm text-gray-500">Записей не найдено.</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="min-w-full divide-y divide-gray-200">
                            <thead className="bg-gray-50">
                                <tr>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Дата</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Время</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Оператор</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Причина</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Комментарий</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Направления</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Добавил</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Фиксация</th>
                                    {canDelete && (
                                        <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600 uppercase">Действия</th>
                                    )}
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-100 bg-white">
                                {rows.map((item) => (
                                    <tr key={item.id}>
                                        <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">{item.date || '—'}</td>
                                        <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">
                                            {item.time_range || ((item.start_time && item.end_time) ? `${item.start_time} - ${item.end_time}` : '—')}
                                        </td>
                                        <td className="px-4 py-3 text-sm text-gray-700">
                                            <div className="font-medium">{item.operator_name || '—'}</div>
                                            <div className="text-xs text-gray-500">{item.direction_name || 'Без направления'}</div>
                                        </td>
                                        <td className="px-4 py-3 text-sm text-gray-700">{item.reason || '—'}</td>
                                        <td className="px-4 py-3 text-sm text-gray-700 max-w-[320px]">
                                            <span className="line-clamp-3">{item.comment || '—'}</span>
                                        </td>
                                        <td className="px-4 py-3 text-sm text-gray-700">
                                            {(item.selected_direction_names || []).length > 0
                                                ? item.selected_direction_names.join(', ')
                                                : '—'}
                                        </td>
                                        <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">
                                            {item.created_by_name || '—'}
                                        </td>
                                        <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">
                                            {item.created_at || '—'}
                                        </td>
                                        {canDelete && (
                                            <td className="px-4 py-3 text-sm text-right whitespace-nowrap">
                                                <button
                                                    type="button"
                                                    onClick={() => handleDeleteIssue(item)}
                                                    disabled={deletingIssueId === item.id}
                                                    className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-medium ${
                                                        deletingIssueId === item.id
                                                            ? 'cursor-not-allowed border-red-100 bg-red-50 text-red-300'
                                                            : 'border-red-200 bg-white text-red-600 hover:bg-red-50'
                                                    }`}
                                                    title="Удалить техсбой"
                                                    aria-label="Удалить техсбой"
                                                >
                                                    <FaIcon className={`fas ${deletingIssueId === item.id ? 'fa-spinner fa-spin' : 'fa-trash'}`} />
                                                    {deletingIssueId === item.id ? 'Удаление...' : 'Удалить'}
                                                </button>
                                            </td>
                                        )}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
};

export default TechnicalIssuesView;

