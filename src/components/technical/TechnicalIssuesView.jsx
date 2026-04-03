import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

const INPUT_CLASS =
    'mt-1 w-full rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-gray-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400';

const MULTI_SELECT_CLASS =
    'mt-1 w-full min-h-[140px] rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-gray-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400';

const LABEL_CLASS = 'text-xs font-semibold uppercase tracking-wide text-blue-900/80';

const toIsoDate = (value = new Date()) => {
    try {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    } catch (error) {
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

const areStringListsEqual = (left, right) => {
    if (!Array.isArray(left) || !Array.isArray(right)) return false;
    if (left.length !== right.length) return false;
    for (let i = 0; i < left.length; i += 1) {
        if (String(left[i]) !== String(right[i])) return false;
    }
    return true;
};

const normalizeFilterPayload = (filters) => ({
    dateFrom: String(filters?.dateFrom || '').trim(),
    dateTo: String(filters?.dateTo || '').trim(),
    operatorId: String(filters?.operatorId || '').trim(),
    reason: String(filters?.reason || '').trim()
});

const areFiltersEqual = (left, right) => {
    const a = normalizeFilterPayload(left);
    const b = normalizeFilterPayload(right);
    return (
        a.dateFrom === b.dateFrom &&
        a.dateTo === b.dateTo &&
        a.operatorId === b.operatorId &&
        a.reason === b.reason
    );
};

const buildFilterQuery = (filters) => {
    const normalized = normalizeFilterPayload(filters);
    const query = new URLSearchParams();
    query.set('limit', '1000');
    if (normalized.dateFrom) query.set('date_from', normalized.dateFrom);
    if (normalized.dateTo) query.set('date_to', normalized.dateTo);
    if (normalized.operatorId) query.set('operator_id', normalized.operatorId);
    if (normalized.reason) query.set('reason', normalized.reason);
    return query;
};

const TechnicalIssueRow = memo(function TechnicalIssueRow({
    item,
    canDelete,
    isDeleting,
    onDelete
}) {
    const selectedDirectionNames = Array.isArray(item?.selected_direction_names)
        ? item.selected_direction_names.filter((name) => String(name || '').trim() !== '')
        : [];

    return (
        <tr className="hover:bg-blue-50/40 transition-colors">
            <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">{item?.date || '—'}</td>
            <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">
                {item?.time_range || ((item?.start_time && item?.end_time) ? `${item.start_time} - ${item.end_time}` : '—')}
            </td>
            <td className="px-4 py-3 text-sm text-gray-700">
                <div className="font-medium">{item?.operator_name || '—'}</div>
                <div className="text-xs text-gray-500">{item?.direction_name || 'Без направления'}</div>
            </td>
            <td className="px-4 py-3 text-sm text-gray-700 max-w-[320px]">
                <span className="line-clamp-3">{item?.reason || '—'}</span>
            </td>
            <td className="px-4 py-3 text-sm text-gray-700 max-w-[320px]">
                <span className="line-clamp-3">{item?.comment || '—'}</span>
            </td>
            <td className="px-4 py-3 text-sm text-gray-700 max-w-[320px]">
                <span className="line-clamp-3">
                    {selectedDirectionNames.length > 0 ? selectedDirectionNames.join(', ') : '—'}
                </span>
            </td>
            <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">
                {item?.created_by_name || '—'}
            </td>
            <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">
                {item?.created_at || '—'}
            </td>
            {canDelete && (
                <td className="px-4 py-3 text-sm text-right whitespace-nowrap">
                    <button
                        type="button"
                        onClick={() => onDelete(item)}
                        disabled={isDeleting}
                        className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-medium ${
                            isDeleting
                                ? 'cursor-not-allowed border-red-100 bg-red-50 text-red-300'
                                : 'border-red-200 bg-white text-red-600 hover:bg-red-50'
                        }`}
                        title="Удалить техсбой"
                        aria-label="Удалить техсбой"
                    >
                        <FaIcon className={`fas ${isDeleting ? 'fa-spinner fa-spin' : 'fa-trash'}`} />
                        {isDeleting ? 'Удаление...' : 'Удалить'}
                    </button>
                </td>
            )}
        </tr>
    );
});

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

    const showToastRef = useRef(showToast);
    useEffect(() => {
        showToastRef.current = showToast;
    }, [showToast]);

    const notify = useCallback((message, type = 'info') => {
        if (typeof showToastRef.current === 'function') {
            showToastRef.current(message, type);
        }
    }, []);

    const buildHeaders = useCallback(() => {
        const baseHeaders = {};
        if (user?.apiKey && String(user.apiKey).trim() !== '') {
            baseHeaders['X-API-Key'] = user.apiKey;
        }
        if (user?.id !== undefined && user?.id !== null && String(user.id).trim() !== '') {
            baseHeaders['X-User-Id'] = user.id;
        }
        if (typeof withAccessTokenHeader === 'function') {
            return withAccessTokenHeader(baseHeaders);
        }
        return baseHeaders;
    }, [user?.apiKey, user?.id, withAccessTokenHeader]);

    const visibleOperators = useMemo(() => {
        const list = Array.isArray(operators) ? operators : [];
        const filtered = list.filter((op) => {
            const opRole = String(op?.role || 'operator').trim().toLowerCase();
            return opRole === 'operator';
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

    const initialFilters = useMemo(
        () => ({
            dateFrom: currentMonthStartIso(),
            dateTo: toIsoDate(new Date()),
            operatorId: '',
            reason: ''
        }),
        []
    );

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

    const [filterDraft, setFilterDraft] = useState(initialFilters);
    const [appliedFilters, setAppliedFilters] = useState(initialFilters);

    const hasPendingFilterChanges = useMemo(
        () => !areFiltersEqual(filterDraft, appliedFilters),
        [filterDraft, appliedFilters]
    );

    const latestRequestIdRef = useRef(0);
    const lastLoadedQueryRef = useRef('');

    useEffect(() => {
        lastLoadedQueryRef.current = '';
    }, [apiBaseUrl, user?.id, user?.apiKey, canView]);

    const fetchReasons = useCallback(async () => {
        if (!canView) return;
        try {
            const response = await axios.get(`${apiBaseUrl}/api/technical_issues/reasons`, {
                headers: buildHeaders()
            });
            const nextReasons = Array.isArray(response?.data?.reasons) ? response.data.reasons : [];
            if (nextReasons.length > 0) {
                setReasons((prev) => (areStringListsEqual(prev, nextReasons) ? prev : nextReasons));
            } else {
                setReasons((prev) => (areStringListsEqual(prev, FALLBACK_TECHNICAL_REASONS) ? prev : FALLBACK_TECHNICAL_REASONS));
            }
        } catch (error) {
            setReasons((prev) => (areStringListsEqual(prev, FALLBACK_TECHNICAL_REASONS) ? prev : FALLBACK_TECHNICAL_REASONS));
        }
    }, [apiBaseUrl, buildHeaders, canView]);

    const fetchRows = useCallback(
        async (filters, { force = false } = {}) => {
            if (!canView) return;

            const query = buildFilterQuery(filters);
            const queryKey = query.toString();
            if (!force && queryKey === lastLoadedQueryRef.current) {
                return;
            }

            lastLoadedQueryRef.current = queryKey;
            const requestId = latestRequestIdRef.current + 1;
            latestRequestIdRef.current = requestId;
            setLoading(true);

            try {
                const response = await axios.get(`${apiBaseUrl}/api/technical_issues?${queryKey}`, {
                    headers: buildHeaders()
                });
                if (requestId !== latestRequestIdRef.current) return;

                const items = Array.isArray(response?.data?.items) ? response.data.items : [];
                const nextTotal = Number(response?.data?.total || items.length || 0);
                setRows(items);
                setTotal(nextTotal);

                const nextReasons = Array.isArray(response?.data?.reasons) ? response.data.reasons : [];
                if (nextReasons.length > 0) {
                    setReasons((prev) => (areStringListsEqual(prev, nextReasons) ? prev : nextReasons));
                }
            } catch (error) {
                if (requestId !== latestRequestIdRef.current) return;
                const message = error?.response?.data?.error || 'Не удалось загрузить список технических причин';
                notify(message, 'error');
            } finally {
                if (requestId === latestRequestIdRef.current) {
                    setLoading(false);
                }
            }
        },
        [apiBaseUrl, buildHeaders, canView, notify]
    );

    useEffect(() => {
        if (!canView) return;
        fetchReasons();
    }, [canView, fetchReasons]);

    useEffect(() => {
        if (!canView) return;
        fetchRows(appliedFilters);
    }, [appliedFilters, canView, fetchRows]);

    const updateDraftFilter = useCallback((field, value) => {
        setFilterDraft((prev) => {
            if (prev[field] === value) return prev;
            return { ...prev, [field]: value };
        });
    }, []);

    const handleCreateIssue = useCallback(
        async (event) => {
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
                await fetchRows(appliedFilters, { force: true });
            } catch (error) {
                const message = error?.response?.data?.error || 'Не удалось сохранить техническую причину';
                notify(message, 'error');
            } finally {
                setSubmitting(false);
            }
        },
        [
            apiBaseUrl,
            appliedFilters,
            buildHeaders,
            canCreate,
            createComment,
            createDate,
            createDirectionIds,
            createEndTime,
            createOperatorIds,
            createReason,
            createStartTime,
            fetchRows,
            notify
        ]
    );

    const handleApplyFilters = useCallback(async () => {
        if (areFiltersEqual(filterDraft, appliedFilters)) {
            await fetchRows(filterDraft, { force: true });
            return;
        }
        setAppliedFilters(normalizeFilterPayload(filterDraft));
    }, [appliedFilters, fetchRows, filterDraft]);

    const handleResetFilters = useCallback(async () => {
        const resetFilters = {
            dateFrom: currentMonthStartIso(),
            dateTo: toIsoDate(new Date()),
            operatorId: '',
            reason: ''
        };
        setFilterDraft(resetFilters);

        if (areFiltersEqual(resetFilters, appliedFilters)) {
            await fetchRows(resetFilters, { force: true });
            return;
        }

        setAppliedFilters(resetFilters);
    }, [appliedFilters, fetchRows]);

    const handleExport = useCallback(async () => {
        if (!canExport) return;
        setExporting(true);
        try {
            const query = buildFilterQuery(appliedFilters);
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
            const anchor = document.createElement('a');
            anchor.href = linkUrl;
            anchor.download = `technical_issues_${toIsoDate(new Date())}.xlsx`;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            window.URL.revokeObjectURL(linkUrl);
        } catch (error) {
            const message = error?.response?.data?.error || 'Не удалось выгрузить Excel';
            notify(message, 'error');
        } finally {
            setExporting(false);
        }
    }, [apiBaseUrl, appliedFilters, buildHeaders, canExport, notify]);

    const handleDeleteIssue = useCallback(
        async (issue) => {
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
                await fetchRows(appliedFilters, { force: true });
            } catch (error) {
                const message = error?.response?.data?.error || 'Не удалось удалить техсбой';
                notify(message, 'error');
            } finally {
                setDeletingIssueId(null);
            }
        },
        [apiBaseUrl, appliedFilters, buildHeaders, canDelete, fetchRows, notify]
    );

    if (!canView) {
        return (
            <div className="mt-6 border-2 border-blue-200 rounded-xl bg-blue-50 shadow-lg p-6">
                <div className="text-sm text-gray-700">Раздел доступен только администраторам и супервайзерам.</div>
            </div>
        );
    }

    return (
        <div className="mt-6 space-y-4">
            <div className="border-2 border-blue-300 rounded-xl bg-blue-50 shadow-lg p-5">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-blue-200 pb-3">
                    <h2 className="text-xl font-bold text-blue-800 tracking-wide uppercase flex items-center gap-2">
                        <FaIcon className="fas fa-tools" />
                        Тех причины
                    </h2>
                    <span className="inline-flex items-center rounded-full border border-blue-300 bg-white px-3 py-1 text-xs font-semibold text-blue-700">
                        Всего записей: {total}
                    </span>
                </div>
                <p className="mt-3 text-sm text-gray-700">
                    Фиксация технических проблем операторов, массовое добавление по направлениям и экспорт журнала.
                </p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <span className="rounded-full bg-white border border-blue-200 px-3 py-1 text-blue-700 font-medium">
                        Операторов: {visibleOperators.length}
                    </span>
                    <span className="rounded-full bg-white border border-blue-200 px-3 py-1 text-blue-700 font-medium">
                        Направлений: {visibleDirections.length}
                    </span>
                    <span className="rounded-full bg-white border border-blue-200 px-3 py-1 text-blue-700 font-medium">
                        Причин: {reasons.length}
                    </span>
                </div>
            </div>

            {canCreate && (
                <form
                    onSubmit={handleCreateIssue}
                    className="border-2 border-blue-300 rounded-xl bg-blue-50 shadow-lg p-5 space-y-4"
                >
                    <h3 className="text-lg font-bold text-blue-800 border-b border-blue-200 pb-2 uppercase tracking-wide">
                        Добавить техпричину
                    </h3>

                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <label className="block">
                            <span className={LABEL_CLASS}>Дата проблемы</span>
                            <input
                                type="date"
                                value={createDate}
                                onChange={(event) => setCreateDate(event.target.value)}
                                className={INPUT_CLASS}
                                required
                            />
                        </label>
                        <label className="block">
                            <span className={LABEL_CLASS}>Время начала</span>
                            <input
                                type="time"
                                value={createStartTime}
                                onChange={(event) => setCreateStartTime(event.target.value)}
                                className={INPUT_CLASS}
                                required
                            />
                        </label>
                        <label className="block">
                            <span className={LABEL_CLASS}>Время окончания</span>
                            <input
                                type="time"
                                value={createEndTime}
                                onChange={(event) => setCreateEndTime(event.target.value)}
                                className={INPUT_CLASS}
                                required
                            />
                        </label>
                        <label className="block">
                            <span className={LABEL_CLASS}>Техническая причина</span>
                            <select
                                value={createReason}
                                onChange={(event) => setCreateReason(event.target.value)}
                                className={INPUT_CLASS}
                                required
                            >
                                <option value="">Выберите причину</option>
                                {reasons.map((reason, index) => (
                                    <option key={`tech-reason-option-${index}-${reason}`} value={reason}>
                                        {reason}
                                    </option>
                                ))}
                            </select>
                        </label>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <label className="block">
                            <span className={LABEL_CLASS}>Операторы (мультивыбор)</span>
                            <select
                                multiple
                                value={createOperatorIds.map(String)}
                                onChange={(event) => setCreateOperatorIds(readMultiSelectIntValues(event))}
                                className={MULTI_SELECT_CLASS}
                            >
                                {visibleOperators.map((operator) => (
                                    <option key={operator.id} value={operator.id}>
                                        {operator.name}
                                    </option>
                                ))}
                            </select>
                            <span className="mt-1 block text-xs text-gray-600">
                                Можно выбрать сотрудников вручную или использовать направления.
                            </span>
                        </label>

                        <label className="block">
                            <span className={LABEL_CLASS}>Направления (мультивыбор)</span>
                            <select
                                multiple
                                value={createDirectionIds.map(String)}
                                onChange={(event) => setCreateDirectionIds(readMultiSelectIntValues(event))}
                                className={MULTI_SELECT_CLASS}
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
                        <span className={LABEL_CLASS}>Комментарий (необязательно)</span>
                        <textarea
                            value={createComment}
                            onChange={(event) => setCreateComment(event.target.value)}
                            rows={3}
                            className={INPUT_CLASS}
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

            <div className="sticky top-0 z-10 border border-blue-200 bg-blue-50 rounded-xl shadow px-4 py-3">
                <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                    <label className="block">
                        <span className={LABEL_CLASS}>Дата с</span>
                        <input
                            type="date"
                            value={filterDraft.dateFrom}
                            onChange={(event) => updateDraftFilter('dateFrom', event.target.value)}
                            className={INPUT_CLASS}
                        />
                    </label>
                    <label className="block">
                        <span className={LABEL_CLASS}>Дата по</span>
                        <input
                            type="date"
                            value={filterDraft.dateTo}
                            onChange={(event) => updateDraftFilter('dateTo', event.target.value)}
                            className={INPUT_CLASS}
                        />
                    </label>
                    <label className="block">
                        <span className={LABEL_CLASS}>Оператор</span>
                        <select
                            value={filterDraft.operatorId}
                            onChange={(event) => updateDraftFilter('operatorId', event.target.value)}
                            className={INPUT_CLASS}
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
                        <span className={LABEL_CLASS}>Причина</span>
                        <select
                            value={filterDraft.reason}
                            onChange={(event) => updateDraftFilter('reason', event.target.value)}
                            className={INPUT_CLASS}
                        >
                            <option value="">Все причины</option>
                            {reasons.map((reason, index) => (
                                <option key={`tech-filter-reason-${index}-${reason}`} value={reason}>
                                    {reason}
                                </option>
                            ))}
                        </select>
                    </label>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2">
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
                        className="inline-flex items-center gap-2 rounded-lg bg-white border border-gray-300 hover:bg-gray-100 px-4 py-2 text-sm text-gray-800"
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
                    <span
                        className={`ml-auto inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${
                            hasPendingFilterChanges
                                ? 'border-amber-200 bg-amber-100 text-amber-800'
                                : 'border-green-200 bg-green-100 text-green-700'
                        }`}
                    >
                        {hasPendingFilterChanges ? 'Фильтры изменены' : 'Фильтры применены'}
                    </span>
                </div>
            </div>

            <div className="mb-10 border-2 border-blue-300 rounded-xl bg-blue-50 shadow-lg overflow-hidden">
                <div className="px-5 py-3 border-b border-blue-200 bg-blue-100/60 flex items-center justify-between">
                    <div className="text-sm font-semibold uppercase tracking-wide text-blue-900">Журнал тех причин</div>
                    <div className="text-xs text-blue-700 font-medium">Всего: {total}</div>
                </div>

                {loading ? (
                    <div className="p-6 text-sm text-gray-600 flex items-center gap-2 bg-white">
                        <FaIcon className="fas fa-spinner fa-spin text-blue-600" />
                        Загрузка...
                    </div>
                ) : rows.length === 0 ? (
                    <div className="p-6 text-sm text-gray-600 bg-white">Записей не найдено.</div>
                ) : (
                    <div className="overflow-x-auto bg-white">
                        <table className="min-w-full divide-y divide-blue-200">
                            <thead className="bg-blue-100/70">
                                <tr>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-blue-900 uppercase">Дата</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-blue-900 uppercase">Время</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-blue-900 uppercase">Оператор</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-blue-900 uppercase">Причина</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-blue-900 uppercase">Комментарий</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-blue-900 uppercase">Направления</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-blue-900 uppercase">Добавил</th>
                                    <th className="px-4 py-3 text-left text-xs font-semibold text-blue-900 uppercase">Фиксация</th>
                                    {canDelete && (
                                        <th className="px-4 py-3 text-right text-xs font-semibold text-blue-900 uppercase">Действия</th>
                                    )}
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-blue-100 bg-white">
                                {rows.map((item, index) => {
                                    const itemId = Number(item?.id);
                                    const key = Number.isFinite(itemId) && itemId > 0
                                        ? `technical-issue-row-${itemId}`
                                        : `technical-issue-row-fallback-${index}-${item?.date || ''}-${item?.operator_name || ''}`;
                                    return (
                                        <TechnicalIssueRow
                                            key={key}
                                            item={item}
                                            canDelete={canDelete}
                                            isDeleting={deletingIssueId === item?.id}
                                            onDelete={handleDeleteIssue}
                                        />
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
};

export default memo(TechnicalIssuesView);
