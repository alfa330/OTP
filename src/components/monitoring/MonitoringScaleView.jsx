import React, { useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';

const FILTER_ITEMS = [
    { id: 'all', label: 'Все' },
    { id: 'files', label: 'С файлами' },
    { id: 'issues', label: 'С проблемами' }
];

const createEmptyCriterionDraft = () => ({
    name: '',
    weight: '',
    value: '',
    isCritical: false,
    hasDeficiency: false,
    deficiencyWeight: '',
    deficiencyDescription: ''
});

const normalizeCriterion = (criterion) => {
    const isCritical = Boolean(criterion?.isCritical);
    const rawWeight = Number(criterion?.weight || 0);
    const deficiency = criterion?.deficiency && typeof criterion.deficiency === 'object'
        ? {
            weight: Number(criterion.deficiency.weight || 0),
            description: String(criterion.deficiency.description || '')
        }
        : null;

    return {
        name: String(criterion?.name || ''),
        weight: isCritical ? 0 : (Number.isFinite(rawWeight) ? rawWeight : 0),
        isCritical,
        value: String(criterion?.value || ''),
        deficiency: deficiency && Number.isFinite(deficiency.weight) && deficiency.weight > 0
            ? deficiency
            : null
    };
};

const normalizeDirections = (directions = []) => (
    (Array.isArray(directions) ? directions : []).map((direction, index) => ({
        id: direction?.id ?? null,
        _localId: String(direction?.id ?? `direction-${index}-${String(direction?.name || 'new').trim().toLowerCase()}`),
        name: String(direction?.name || ''),
        hasFileUpload: direction?.hasFileUpload !== false,
        criteria: Array.isArray(direction?.criteria) ? direction.criteria.map(normalizeCriterion) : [],
        isActive: direction?.isActive !== false
    }))
);

const serializeDirections = (directions = []) => (
    (Array.isArray(directions) ? directions : []).map((direction) => ({
        name: String(direction?.name || '').trim(),
        hasFileUpload: direction?.hasFileUpload !== false,
        criteria: (Array.isArray(direction?.criteria) ? direction.criteria : []).map((criterion) => ({
            name: String(criterion?.name || '').trim(),
            weight: criterion?.isCritical ? 0 : Number(criterion?.weight || 0),
            isCritical: Boolean(criterion?.isCritical),
            value: String(criterion?.value || '').trim() || 'Нет описания',
            deficiency: criterion?.deficiency && Number(criterion.deficiency.weight || 0) > 0
                ? {
                    weight: Number(criterion.deficiency.weight || 0),
                    description: String(criterion.deficiency.description || '').trim() || 'Нет описания'
                }
                : null
        }))
    }))
);

const getWeightedTotal = (direction) => (
    (Array.isArray(direction?.criteria) ? direction.criteria : [])
        .filter((criterion) => !criterion?.isCritical)
        .reduce((sum, criterion) => sum + Number(criterion?.weight || 0), 0)
);

const getDirectionMetrics = (direction) => {
    const criteria = Array.isArray(direction?.criteria) ? direction.criteria : [];
    const weightedCriteria = criteria.filter((criterion) => !criterion?.isCritical);
    const weightedTotal = getWeightedTotal(direction);
    const criticalCount = criteria.filter((criterion) => criterion?.isCritical).length;
    const deficiencyCount = criteria.filter((criterion) => criterion?.deficiency).length;
    const issues = [];

    if (!String(direction?.name || '').trim()) issues.push('Нужно название направления');
    if (criteria.length === 0) issues.push('Нет критериев');
    if (weightedCriteria.length > 0 && weightedTotal !== 100) issues.push(`Вес: ${weightedTotal}/100`);

    return {
        criteriaCount: criteria.length,
        weightedCount: weightedCriteria.length,
        weightedTotal,
        criticalCount,
        deficiencyCount,
        isBalanced: weightedCriteria.length === 0 || weightedTotal === 100,
        issues
    };
};

const scoreToneClass = (weightedTotal, weightedCount) => {
    if (weightedCount === 0) return 'text-slate-500';
    if (weightedTotal === 100) return 'text-emerald-600';
    if (weightedTotal > 100) return 'text-red-600';
    return 'text-amber-600';
};

const MonitoringScaleView = ({
    directions = [],
    loading = false,
    onRefresh,
    onSave,
    showToast,
    canEdit = true
}) => {
    const tempDirectionIdRef = useRef(1);
    const sourceDirections = useMemo(() => normalizeDirections(directions), [directions]);
    const [draftDirections, setDraftDirections] = useState(sourceDirections);
    const [selectedDirectionId, setSelectedDirectionId] = useState(sourceDirections[0]?._localId || '');
    const [searchQuery, setSearchQuery] = useState('');
    const [activeFilter, setActiveFilter] = useState('all');
    const [criterionDraft, setCriterionDraft] = useState(createEmptyCriterionDraft());
    const [editingCriterionIndex, setEditingCriterionIndex] = useState(null);

    const notify = (message, type = 'info') => {
        if (typeof showToast === 'function') {
            showToast(message, type);
            return;
        }
        if (type === 'error') {
            console.error(message);
            return;
        }
        console.warn(message);
    };

    useEffect(() => {
        setDraftDirections(sourceDirections);
        setSelectedDirectionId((prev) => {
            if (sourceDirections.length === 0) return '';
            if (sourceDirections.some((direction) => direction._localId === prev)) return prev;

            const previousDirection = draftDirections.find((direction) => direction._localId === prev);
            if (previousDirection?.id != null) {
                const byId = sourceDirections.find((direction) => Number(direction.id) === Number(previousDirection.id));
                if (byId) return byId._localId;
            }
            if (previousDirection?.name) {
                const byName = sourceDirections.find((direction) => direction.name === previousDirection.name);
                if (byName) return byName._localId;
            }
            return sourceDirections[0]._localId;
        });
    }, [sourceDirections]);

    const selectedDirection = useMemo(
        () => draftDirections.find((direction) => direction._localId === selectedDirectionId) || null,
        [draftDirections, selectedDirectionId]
    );

    useEffect(() => {
        setCriterionDraft(createEmptyCriterionDraft());
        setEditingCriterionIndex(null);
    }, [selectedDirectionId]);

    const selectedMetrics = useMemo(() => getDirectionMetrics(selectedDirection), [selectedDirection]);

    const sourceSerialized = useMemo(
        () => JSON.stringify(serializeDirections(sourceDirections)),
        [sourceDirections]
    );
    const draftSerialized = useMemo(
        () => JSON.stringify(serializeDirections(draftDirections)),
        [draftDirections]
    );
    const hasUnsavedChanges = sourceSerialized !== draftSerialized;

    const aggregateStats = useMemo(() => {
        const metrics = draftDirections.map((direction) => getDirectionMetrics(direction));
        return {
            directionsCount: draftDirections.length,
            criteriaCount: metrics.reduce((sum, item) => sum + item.criteriaCount, 0),
            criticalCount: metrics.reduce((sum, item) => sum + item.criticalCount, 0),
            deficiencyCount: metrics.reduce((sum, item) => sum + item.deficiencyCount, 0),
            balancedCount: metrics.filter((item) => item.isBalanced).length,
            issuesCount: metrics.reduce((sum, item) => sum + item.issues.length, 0)
        };
    }, [draftDirections]);

    const visibleDirections = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        return draftDirections.filter((direction) => {
            const metrics = getDirectionMetrics(direction);
            const searchableText = [
                direction.name,
                ...(Array.isArray(direction.criteria) ? direction.criteria.map((criterion) => criterion?.name || '') : [])
            ].join(' ').toLowerCase();

            const matchesQuery = !query || searchableText.includes(query);
            const matchesFilter =
                activeFilter === 'all' ||
                (activeFilter === 'files' && direction.hasFileUpload) ||
                (activeFilter === 'issues' && metrics.issues.length > 0);

            return matchesQuery && matchesFilter;
        });
    }, [activeFilter, draftDirections, searchQuery]);

    const updateSelectedDirection = (updater) => {
        if (!selectedDirectionId) return;
        setDraftDirections((prev) => prev.map((direction) => (
            direction._localId === selectedDirectionId ? updater(direction) : direction
        )));
    };

    const handleAddDirection = () => {
        const localId = `temp-direction-${tempDirectionIdRef.current++}`;
        setDraftDirections((prev) => [
            ...prev,
            { id: null, _localId: localId, name: '', hasFileUpload: true, criteria: [], isActive: true }
        ]);
        setSelectedDirectionId(localId);
    };

    const handleDeleteDirection = (direction) => {
        if (!direction) return;
        const directionName = String(direction.name || 'без названия').trim() || 'без названия';
        const isConfirmed = typeof window === 'undefined'
            ? true
            : window.confirm(`Удалить направление «${directionName}» со всеми критериями?`);

        if (!isConfirmed) return;

        setDraftDirections((prev) => {
            const nextDirections = prev.filter((item) => item._localId !== direction._localId);
            if (selectedDirectionId === direction._localId) {
                setSelectedDirectionId(nextDirections[0]?._localId || '');
            }
            return nextDirections;
        });
    };

    const resetCriterionEditor = () => {
        setCriterionDraft(createEmptyCriterionDraft());
        setEditingCriterionIndex(null);
    };

    const validateCriterionDraft = () => {
        if (!selectedDirection) {
            notify('Сначала выберите направление.', 'error');
            return null;
        }

        const name = criterionDraft.name.trim();
        if (!name) {
            notify('Введите название критерия.', 'error');
            return null;
        }

        const baseCriterion = editingCriterionIndex != null
            ? selectedDirection.criteria[editingCriterionIndex]
            : null;
        const reservedWeight = baseCriterion && !baseCriterion.isCritical ? Number(baseCriterion.weight || 0) : 0;
        const currentWeightedTotal = getWeightedTotal(selectedDirection) - reservedWeight;

        let normalizedWeight = 0;
        if (!criterionDraft.isCritical) {
            normalizedWeight = Number(criterionDraft.weight);
            if (!Number.isFinite(normalizedWeight) || normalizedWeight <= 0) {
                notify('Вес критерия должен быть больше 0.', 'error');
                return null;
            }
            if (currentWeightedTotal + normalizedWeight > 100) {
                notify(`Сумма весов в направлении не может превышать 100. Доступно: ${Math.max(0, 100 - currentWeightedTotal)}%.`, 'error');
                return null;
            }
        }

        let deficiency = null;
        if (!criterionDraft.isCritical && criterionDraft.hasDeficiency) {
            const deficiencyWeight = Number(criterionDraft.deficiencyWeight);
            if (!Number.isFinite(deficiencyWeight) || deficiencyWeight <= 0) {
                notify('Вес недочета должен быть больше 0.', 'error');
                return null;
            }
            if (deficiencyWeight > normalizedWeight) {
                notify('Вес недочета не может быть больше веса критерия.', 'error');
                return null;
            }
            deficiency = {
                weight: deficiencyWeight,
                description: criterionDraft.deficiencyDescription.trim() || 'Нет описания'
            };
        }

        return {
            name,
            weight: criterionDraft.isCritical ? 0 : normalizedWeight,
            isCritical: criterionDraft.isCritical,
            value: criterionDraft.value.trim() || 'Нет описания',
            deficiency
        };
    };

    const handleSubmitCriterion = () => {
        const nextCriterion = validateCriterionDraft();
        if (!nextCriterion) return;

        updateSelectedDirection((direction) => {
            const criteria = Array.isArray(direction.criteria) ? [...direction.criteria] : [];
            if (editingCriterionIndex != null) {
                criteria[editingCriterionIndex] = nextCriterion;
            } else {
                criteria.push(nextCriterion);
            }
            return { ...direction, criteria };
        });

        resetCriterionEditor();
    };

    const handleEditCriterion = (criterion, index) => {
        setCriterionDraft({
            name: String(criterion?.name || ''),
            weight: criterion?.isCritical ? '' : String(criterion?.weight ?? ''),
            value: String(criterion?.value || ''),
            isCritical: Boolean(criterion?.isCritical),
            hasDeficiency: Boolean(criterion?.deficiency),
            deficiencyWeight: criterion?.deficiency ? String(criterion.deficiency.weight ?? '') : '',
            deficiencyDescription: criterion?.deficiency ? String(criterion.deficiency.description || '') : ''
        });
        setEditingCriterionIndex(index);
    };

    const handleDeleteCriterion = (index) => {
        if (!selectedDirection) return;
        const criterionName = selectedDirection.criteria[index]?.name || 'без названия';
        const isConfirmed = typeof window === 'undefined'
            ? true
            : window.confirm(`Удалить критерий «${criterionName}»?`);
        if (!isConfirmed) return;

        updateSelectedDirection((direction) => ({
            ...direction,
            criteria: direction.criteria.filter((_, criterionIndex) => criterionIndex !== index)
        }));

        if (editingCriterionIndex === index) {
            resetCriterionEditor();
        }
    };

    const validateBeforeSave = () => {
        const cleanedDirections = serializeDirections(draftDirections);
        if (cleanedDirections.length === 0) {
            notify('Добавьте хотя бы одно направление.', 'error');
            return null;
        }

        const duplicateNames = new Set();
        const seenNames = new Set();
        cleanedDirections.forEach((direction) => {
            const normalizedName = direction.name.toLowerCase();
            if (!normalizedName) return;
            if (seenNames.has(normalizedName)) {
                duplicateNames.add(direction.name);
            } else {
                seenNames.add(normalizedName);
            }
        });

        if (duplicateNames.size > 0) {
            notify(`Повторяются названия направлений: ${Array.from(duplicateNames).join(', ')}.`, 'error');
            return null;
        }

        for (const direction of cleanedDirections) {
            if (!direction.name) {
                notify('У каждого направления должно быть название.', 'error');
                return null;
            }

            const weightedCriteria = direction.criteria.filter((criterion) => !criterion.isCritical);
            const weightedTotal = weightedCriteria.reduce((sum, criterion) => sum + Number(criterion.weight || 0), 0);
            if (weightedCriteria.length > 0 && weightedTotal !== 100) {
                notify(`В направлении «${direction.name}» сумма весов должна быть ровно 100. Сейчас: ${weightedTotal}.`, 'error');
                return null;
            }
        }

        return cleanedDirections;
    };

    const handleSave = async () => {
        const cleanedDirections = validateBeforeSave();
        if (!cleanedDirections || typeof onSave !== 'function') return;
        await onSave(cleanedDirections);
    };

    const remainingWeight = useMemo(() => {
        if (!selectedDirection) return 100;
        const originalCriterion = editingCriterionIndex != null
            ? selectedDirection.criteria[editingCriterionIndex]
            : null;
        const reservedWeight = originalCriterion && !originalCriterion.isCritical ? Number(originalCriterion.weight || 0) : 0;
        return Math.max(0, 100 - (getWeightedTotal(selectedDirection) - reservedWeight));
    }, [editingCriterionIndex, selectedDirection]);

    return (
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="px-6 py-5 border-b border-slate-200 bg-gradient-to-r from-slate-50 via-white to-slate-50">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
                    <div>
                        <div className="flex items-center gap-3">
                            <div className="w-11 h-11 rounded-2xl bg-blue-50 text-blue-600 border border-blue-100 flex items-center justify-center">
                                <FaIcon className="fas fa-sliders-h text-lg" />
                            </div>
                            <div>
                                <h2 className="text-xl font-semibold text-slate-900">Мониторинговая шкала</h2>
                                <p className="text-sm text-slate-500 mt-0.5">
                                    Полноценный рабочий раздел для направлений, критериев и контроля веса шкалы.
                                </p>
                            </div>
                        </div>
                        {loading && (
                            <p className="mt-3 text-xs font-medium text-blue-600 flex items-center gap-2">
                                <FaIcon className="fas fa-spinner fa-spin" />
                                Обновляем данные шкалы...
                            </p>
                        )}
                    </div>

                    <div className="flex flex-wrap gap-2">
                        <button
                            type="button"
                            onClick={() => onRefresh?.()}
                            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 transition"
                        >
                            <FaIcon className="fas fa-rotate-right" />
                            Обновить
                        </button>
                        <button
                            type="button"
                            onClick={handleAddDirection}
                            disabled={!canEdit}
                            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100 transition disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            <FaIcon className="fas fa-plus" />
                            Добавить направление
                        </button>
                        <button
                            type="button"
                            onClick={() => { void handleSave(); }}
                            disabled={!canEdit || !hasUnsavedChanges || loading}
                            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            <FaIcon className="fas fa-save" />
                            Сохранить шкалу
                        </button>
                    </div>
                </div>

                <div className="mt-5 flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                    <div className="relative w-full xl:max-w-md">
                        <FaIcon className="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm" />
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(event) => setSearchQuery(event.target.value)}
                            placeholder="Поиск по направлениям и критериям..."
                            className="w-full pl-10 pr-4 py-2.5 rounded-xl border border-slate-200 bg-white text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                        />
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                        {FILTER_ITEMS.map((filter) => (
                            <button
                                key={filter.id}
                                type="button"
                                onClick={() => setActiveFilter(filter.id)}
                                className={`px-3 py-2 rounded-xl text-sm font-medium transition ${
                                    activeFilter === filter.id
                                        ? 'bg-slate-900 text-white shadow-sm'
                                        : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50'
                                }`}
                            >
                                {filter.label}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            <div className="bg-slate-100 flex flex-col lg:flex-row gap-px border-b border-slate-200">
                {[
                    {
                        label: 'Направления',
                        value: aggregateStats.directionsCount,
                        sub: 'активных блоков шкалы',
                        icon: 'fas fa-layer-group',
                        tone: 'text-blue-600 bg-blue-50'
                    },
                    {
                        label: 'Критерии',
                        value: aggregateStats.criteriaCount,
                        sub: `${aggregateStats.criticalCount} критических`,
                        icon: 'fas fa-list-check',
                        tone: 'text-emerald-600 bg-emerald-50'
                    },
                    {
                        label: 'Сбалансированы',
                        value: `${aggregateStats.balancedCount}/${aggregateStats.directionsCount || 0}`,
                        sub: 'с весом 100/100',
                        icon: 'fas fa-scale-balanced',
                        tone: 'text-violet-600 bg-violet-50'
                    },
                    {
                        label: 'Проблемы',
                        value: aggregateStats.issuesCount,
                        sub: `${aggregateStats.deficiencyCount} недочетов в шкале`,
                        icon: 'fas fa-triangle-exclamation',
                        tone: 'text-amber-600 bg-amber-50'
                    }
                ].map((item) => (
                    <div key={item.label} className="flex-1 min-w-0 bg-white px-6 py-4 flex items-center gap-3">
                        <div className={`w-10 h-10 rounded-2xl flex items-center justify-center border border-current/10 ${item.tone}`}>
                            <FaIcon className={item.icon} />
                        </div>
                        <div className="min-w-0">
                            <div className="text-xs uppercase tracking-[0.18em] text-slate-500 font-semibold">{item.label}</div>
                            <div className="text-2xl font-semibold text-slate-900 leading-none mt-1">{item.value}</div>
                            <div className="text-xs text-slate-500 mt-1">{item.sub}</div>
                        </div>
                    </div>
                ))}
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-[340px_minmax(0,1fr)]">
                <aside className="border-r border-slate-200 bg-slate-50/70">
                    <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
                        <div>
                            <h3 className="text-sm font-semibold text-slate-900">Направления шкалы</h3>
                            <p className="text-xs text-slate-500 mt-0.5">{visibleDirections.length} из {draftDirections.length}</p>
                        </div>
                        {hasUnsavedChanges && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200 text-xs font-semibold">
                                <FaIcon className="fas fa-pen" />
                                Есть изменения
                            </span>
                        )}
                    </div>

                    <div className="max-h-[calc(100vh-320px)] overflow-y-auto">
                        {loading && draftDirections.length === 0 ? (
                            <div className="p-4 space-y-3">
                                {Array.from({ length: 4 }).map((_, index) => (
                                    <div key={index} className="rounded-2xl border border-slate-200 bg-white p-4 animate-pulse">
                                        <div className="h-4 w-2/3 rounded bg-slate-200" />
                                        <div className="h-3 w-full rounded bg-slate-100 mt-3" />
                                        <div className="h-2 w-full rounded-full bg-slate-100 mt-4" />
                                    </div>
                                ))}
                            </div>
                        ) : visibleDirections.length === 0 ? (
                            <div className="px-5 py-10 text-center">
                                <div className="w-14 h-14 mx-auto rounded-2xl bg-slate-100 text-slate-400 flex items-center justify-center">
                                    <FaIcon className="fas fa-folder-open" />
                                </div>
                                <h4 className="mt-4 text-sm font-semibold text-slate-900">Ничего не найдено</h4>
                                <p className="mt-1 text-sm text-slate-500">
                                    Сбросьте поиск или добавьте новое направление для этой шкалы.
                                </p>
                            </div>
                        ) : (
                            <div className="p-4 space-y-3">
                                {visibleDirections.map((direction) => {
                                    const metrics = getDirectionMetrics(direction);
                                    const isSelected = direction._localId === selectedDirectionId;
                                    return (
                                        <button
                                            key={direction._localId}
                                            type="button"
                                            onClick={() => setSelectedDirectionId(direction._localId)}
                                            className={`w-full text-left rounded-2xl border p-4 transition ${
                                                isSelected
                                                    ? 'border-blue-300 bg-blue-50/70 shadow-sm'
                                                    : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'
                                            }`}
                                        >
                                            <div className="flex items-start justify-between gap-3">
                                                <div className="min-w-0">
                                                    <div className="text-sm font-semibold text-slate-900 truncate">
                                                        {direction.name.trim() || 'Новое направление'}
                                                    </div>
                                                    <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-2">
                                                        <span>{metrics.criteriaCount} критериев</span>
                                                        <span>{metrics.criticalCount} критических</span>
                                                        <span>{metrics.deficiencyCount} недочетов</span>
                                                    </div>
                                                </div>
                                                <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold ${
                                                    direction.hasFileUpload
                                                        ? 'bg-blue-100 text-blue-700'
                                                        : 'bg-slate-100 text-slate-600'
                                                }`}>
                                                    <FaIcon className={direction.hasFileUpload ? 'fas fa-file-arrow-up' : 'fas fa-file-circle-minus'} />
                                                    {direction.hasFileUpload ? 'Файл' : 'Без файла'}
                                                </span>
                                            </div>

                                            <div className="mt-4">
                                                <div className="flex items-center justify-between text-xs font-medium">
                                                    <span className="text-slate-500">Вес некритических критериев</span>
                                                    <span className={scoreToneClass(metrics.weightedTotal, metrics.weightedCount)}>
                                                        {metrics.weightedCount > 0 ? `${metrics.weightedTotal}/100` : 'не используется'}
                                                    </span>
                                                </div>
                                                <div className="mt-2 h-2 rounded-full bg-slate-100 overflow-hidden">
                                                    <div
                                                        className={`h-full rounded-full ${
                                                            metrics.weightedCount === 0
                                                                ? 'bg-slate-300'
                                                                : metrics.weightedTotal === 100
                                                                    ? 'bg-emerald-500'
                                                                    : metrics.weightedTotal > 100
                                                                        ? 'bg-red-500'
                                                                        : 'bg-amber-500'
                                                        }`}
                                                        style={{ width: `${Math.min(100, Math.max(0, metrics.weightedTotal))}%` }}
                                                    />
                                                </div>
                                            </div>

                                            <div className="mt-3 min-h-[20px]">
                                                {metrics.issues.length > 0 ? (
                                                    <div className="text-xs text-amber-700 flex items-center gap-2">
                                                        <FaIcon className="fas fa-circle-exclamation" />
                                                        {metrics.issues[0]}
                                                    </div>
                                                ) : (
                                                    <div className="text-xs text-emerald-700 flex items-center gap-2">
                                                        <FaIcon className="fas fa-circle-check" />
                                                        Направление готово к сохранению
                                                    </div>
                                                )}
                                            </div>
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                </aside>

                <section className="min-w-0 bg-white">
                    {!selectedDirection ? (
                        <div className="px-6 py-16 text-center">
                            <div className="w-16 h-16 mx-auto rounded-3xl bg-slate-100 text-slate-400 flex items-center justify-center">
                                <FaIcon className="fas fa-sliders" />
                            </div>
                            <h3 className="mt-5 text-lg font-semibold text-slate-900">Выберите направление</h3>
                            <p className="mt-2 text-sm text-slate-500 max-w-md mx-auto">
                                Слева отображается список направлений. После выбора можно редактировать параметры направления,
                                добавлять критерии и следить за балансом весов в одной рабочей области.
                            </p>
                        </div>
                    ) : (
                        <div className="p-6 space-y-6">
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
                                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500 font-semibold">Текущее направление</div>
                                    <div className="mt-2 text-lg font-semibold text-slate-900">
                                        {selectedDirection.name.trim() || 'Новое направление'}
                                    </div>
                                    <div className="mt-2 text-sm text-slate-500">
                                        {selectedDirection.hasFileUpload ? 'Требует загрузку файла при оценке' : 'Работает без загрузки файла'}
                                    </div>
                                </div>
                                <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
                                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500 font-semibold">Вес шкалы</div>
                                    <div className={`mt-2 text-lg font-semibold ${scoreToneClass(selectedMetrics.weightedTotal, selectedMetrics.weightedCount)}`}>
                                        {selectedMetrics.weightedCount > 0 ? `${selectedMetrics.weightedTotal}/100` : 'Без весовых критериев'}
                                    </div>
                                    <div className="mt-2 text-sm text-slate-500">
                                        {selectedMetrics.isBalanced ? 'Баланс в порядке' : 'Нужно довести сумму до 100'}
                                    </div>
                                </div>
                                <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
                                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500 font-semibold">Состав критериев</div>
                                    <div className="mt-2 text-lg font-semibold text-slate-900">
                                        {selectedMetrics.criteriaCount} шт.
                                    </div>
                                    <div className="mt-2 text-sm text-slate-500">
                                        {selectedMetrics.criticalCount} критических, {selectedMetrics.deficiencyCount} с недочетами
                                    </div>
                                </div>
                            </div>

                            <div className="grid grid-cols-1 2xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] gap-6">
                                <div className="rounded-2xl border border-slate-200 overflow-hidden">
                                    <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex items-center justify-between gap-3">
                                        <div>
                                            <h3 className="text-sm font-semibold text-slate-900">Параметры направления</h3>
                                            <p className="text-xs text-slate-500 mt-0.5">Название, работа с файлами и быстрые действия.</p>
                                        </div>
                                        <button
                                            type="button"
                                            onClick={() => handleDeleteDirection(selectedDirection)}
                                            disabled={!canEdit}
                                            className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-red-200 bg-red-50 text-red-700 hover:bg-red-100 transition disabled:opacity-50 disabled:cursor-not-allowed"
                                        >
                                            <FaIcon className="fas fa-trash" />
                                            Удалить
                                        </button>
                                    </div>

                                    <div className="p-5 space-y-5">
                                        <div>
                                            <label className="block text-sm font-medium text-slate-700 mb-2">Название направления</label>
                                            <input
                                                type="text"
                                                value={selectedDirection.name}
                                                onChange={(event) => updateSelectedDirection((direction) => ({ ...direction, name: event.target.value }))}
                                                disabled={!canEdit}
                                                placeholder="Например, Вежливость и скрипт"
                                                className="w-full px-4 py-3 rounded-xl border border-slate-200 bg-white text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-50"
                                            />
                                        </div>

                                        <label className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={selectedDirection.hasFileUpload}
                                                onChange={(event) => updateSelectedDirection((direction) => ({ ...direction, hasFileUpload: event.target.checked }))}
                                                disabled={!canEdit}
                                                className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                                            />
                                            <span>
                                                <span className="block text-sm font-medium text-slate-800">Требовать загрузку файла</span>
                                                <span className="block text-xs text-slate-500 mt-1">
                                                    Включите, если при оценке по этому направлению обязательно нужен файл или аудио.
                                                </span>
                                            </span>
                                        </label>

                                        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-4">
                                            <div className="flex items-start justify-between gap-3">
                                                <div>
                                                    <div className="text-sm font-semibold text-slate-900">Проверка перед сохранением</div>
                                                    <div className="text-xs text-slate-500 mt-1">
                                                        Для направлений с весовыми критериями сумма должна быть ровно 100.
                                                    </div>
                                                </div>
                                                <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold ${
                                                    selectedMetrics.isBalanced ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
                                                }`}>
                                                    <FaIcon className={selectedMetrics.isBalanced ? 'fas fa-check' : 'fas fa-clock'} />
                                                    {selectedMetrics.isBalanced ? 'Готово' : 'Нужно проверить'}
                                                </span>
                                            </div>
                                            <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                                                <div className="rounded-xl bg-slate-50 px-3 py-3">
                                                    <div className="text-xs text-slate-500">Весовые критерии</div>
                                                    <div className="mt-1 font-semibold text-slate-900">{selectedMetrics.weightedCount}</div>
                                                </div>
                                                <div className="rounded-xl bg-slate-50 px-3 py-3">
                                                    <div className="text-xs text-slate-500">Сумма веса</div>
                                                    <div className={`mt-1 font-semibold ${scoreToneClass(selectedMetrics.weightedTotal, selectedMetrics.weightedCount)}`}>
                                                        {selectedMetrics.weightedTotal}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <div className="rounded-2xl border border-slate-200 overflow-hidden">
                                    <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex items-center justify-between gap-3">
                                        <div>
                                            <h3 className="text-sm font-semibold text-slate-900">
                                                {editingCriterionIndex != null ? 'Редактирование критерия' : 'Новый критерий'}
                                            </h3>
                                            <p className="text-xs text-slate-500 mt-0.5">
                                                Добавляйте обычные, критические критерии и недочеты без выхода из раздела.
                                            </p>
                                        </div>
                                        {editingCriterionIndex != null && (
                                            <button
                                                type="button"
                                                onClick={resetCriterionEditor}
                                                className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 transition"
                                            >
                                                <FaIcon className="fas fa-xmark" />
                                                Отмена
                                            </button>
                                        )}
                                    </div>

                                    <div className="p-5 space-y-4">
                                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                                            <div>
                                                <label className="block text-sm font-medium text-slate-700 mb-2">Название критерия</label>
                                                <input
                                                    type="text"
                                                    value={criterionDraft.name}
                                                    onChange={(event) => setCriterionDraft((prev) => ({ ...prev, name: event.target.value }))}
                                                    disabled={!canEdit}
                                                    placeholder="Например, Проверил потребность"
                                                    className="w-full px-4 py-3 rounded-xl border border-slate-200 bg-white text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-50"
                                                />
                                            </div>

                                            <div>
                                                <label className="block text-sm font-medium text-slate-700 mb-2">
                                                    {criterionDraft.isCritical ? 'Вес не требуется' : `Вес критерия (доступно до ${remainingWeight}%)`}
                                                </label>
                                                <input
                                                    type="number"
                                                    value={criterionDraft.weight}
                                                    onChange={(event) => setCriterionDraft((prev) => ({ ...prev, weight: event.target.value }))}
                                                    disabled={!canEdit || criterionDraft.isCritical}
                                                    min="1"
                                                    placeholder={criterionDraft.isCritical ? 'Критический критерий' : 'Введите вес'}
                                                    className="w-full px-4 py-3 rounded-xl border border-slate-200 bg-white text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-50"
                                                />
                                            </div>
                                        </div>

                                        <div>
                                            <label className="block text-sm font-medium text-slate-700 mb-2">Описание критерия</label>
                                            <textarea
                                                rows={4}
                                                value={criterionDraft.value}
                                                onChange={(event) => setCriterionDraft((prev) => ({ ...prev, value: event.target.value }))}
                                                disabled={!canEdit}
                                                placeholder="Подробно опишите, как должен оцениваться критерий."
                                                className="w-full px-4 py-3 rounded-xl border border-slate-200 bg-white text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 resize-y disabled:bg-slate-50"
                                            />
                                        </div>

                                        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
                                            <label className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 cursor-pointer flex-1">
                                                <input
                                                    type="checkbox"
                                                    checked={criterionDraft.isCritical}
                                                    onChange={(event) => setCriterionDraft((prev) => ({
                                                        ...prev,
                                                        isCritical: event.target.checked,
                                                        weight: event.target.checked ? '' : prev.weight,
                                                        hasDeficiency: event.target.checked ? false : prev.hasDeficiency,
                                                        deficiencyWeight: event.target.checked ? '' : prev.deficiencyWeight,
                                                        deficiencyDescription: event.target.checked ? '' : prev.deficiencyDescription
                                                    }))}
                                                    disabled={!canEdit}
                                                    className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                                                />
                                                <span>
                                                    <span className="block text-sm font-medium text-slate-800">Критический критерий</span>
                                                    <span className="block text-xs text-slate-500 mt-1">
                                                        Ошибка по такому критерию обнуляет результат и не использует отдельный вес.
                                                    </span>
                                                </span>
                                            </label>

                                            <label className={`flex items-start gap-3 rounded-2xl border px-4 py-3 cursor-pointer flex-1 ${
                                                criterionDraft.isCritical ? 'border-slate-200 bg-slate-50 opacity-60' : 'border-slate-200 bg-slate-50'
                                            }`}>
                                                <input
                                                    type="checkbox"
                                                    checked={criterionDraft.hasDeficiency}
                                                    onChange={(event) => setCriterionDraft((prev) => ({
                                                        ...prev,
                                                        hasDeficiency: event.target.checked,
                                                        deficiencyWeight: event.target.checked ? prev.deficiencyWeight : '',
                                                        deficiencyDescription: event.target.checked ? prev.deficiencyDescription : ''
                                                    }))}
                                                    disabled={!canEdit || criterionDraft.isCritical}
                                                    className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                                                />
                                                <span>
                                                    <span className="block text-sm font-medium text-slate-800">Добавить недочет</span>
                                                    <span className="block text-xs text-slate-500 mt-1">
                                                        Для мягкой ошибки можно задать отдельный штраф и пояснение.
                                                    </span>
                                                </span>
                                            </label>
                                        </div>

                                        {criterionDraft.hasDeficiency && !criterionDraft.isCritical && (
                                            <div className="grid grid-cols-1 lg:grid-cols-[180px_minmax(0,1fr)] gap-4 rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
                                                <div>
                                                    <label className="block text-sm font-medium text-amber-900 mb-2">Вес недочета</label>
                                                    <input
                                                        type="number"
                                                        min="1"
                                                        value={criterionDraft.deficiencyWeight}
                                                        onChange={(event) => setCriterionDraft((prev) => ({ ...prev, deficiencyWeight: event.target.value }))}
                                                        disabled={!canEdit}
                                                        className="w-full px-4 py-3 rounded-xl border border-amber-200 bg-white text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-amber-400"
                                                    />
                                                </div>
                                                <div>
                                                    <label className="block text-sm font-medium text-amber-900 mb-2">Описание недочета</label>
                                                    <textarea
                                                        rows={3}
                                                        value={criterionDraft.deficiencyDescription}
                                                        onChange={(event) => setCriterionDraft((prev) => ({ ...prev, deficiencyDescription: event.target.value }))}
                                                        disabled={!canEdit}
                                                        placeholder="Когда ставится недочет и чем он отличается от полной ошибки."
                                                        className="w-full px-4 py-3 rounded-xl border border-amber-200 bg-white text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-amber-400 resize-y"
                                                    />
                                                </div>
                                            </div>
                                        )}

                                        <div className="flex flex-wrap gap-3">
                                            <button
                                                type="button"
                                                onClick={handleSubmitCriterion}
                                                disabled={!canEdit}
                                                className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
                                            >
                                                <FaIcon className={editingCriterionIndex != null ? 'fas fa-floppy-disk' : 'fas fa-plus'} />
                                                {editingCriterionIndex != null ? 'Сохранить изменения' : 'Добавить критерий'}
                                            </button>
                                            <div className="inline-flex items-center gap-2 px-3 py-2 rounded-xl bg-slate-100 text-slate-600 text-sm">
                                                <FaIcon className="fas fa-circle-info" />
                                                Свободный вес для обычных критериев: {remainingWeight}%
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <div className="rounded-2xl border border-slate-200 overflow-hidden">
                                <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                    <div>
                                        <h3 className="text-sm font-semibold text-slate-900">Критерии направления</h3>
                                        <p className="text-xs text-slate-500 mt-0.5">
                                            Табличный обзор по критериям, весу, статусу и описанию.
                                        </p>
                                    </div>
                                    <div className="flex flex-wrap gap-2 text-xs">
                                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-slate-100 text-slate-600 font-semibold">
                                            <FaIcon className="fas fa-list" />
                                            {selectedMetrics.criteriaCount} всего
                                        </span>
                                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-red-50 text-red-700 font-semibold">
                                            <FaIcon className="fas fa-bolt" />
                                            {selectedMetrics.criticalCount} критических
                                        </span>
                                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-amber-50 text-amber-700 font-semibold">
                                            <FaIcon className="fas fa-triangle-exclamation" />
                                            {selectedMetrics.deficiencyCount} недочетов
                                        </span>
                                    </div>
                                </div>

                                {selectedDirection.criteria.length === 0 ? (
                                    <div className="px-6 py-14 text-center">
                                        <div className="w-14 h-14 mx-auto rounded-2xl bg-slate-100 text-slate-400 flex items-center justify-center">
                                            <FaIcon className="fas fa-list-check" />
                                        </div>
                                        <h4 className="mt-4 text-sm font-semibold text-slate-900">Критерии еще не добавлены</h4>
                                        <p className="mt-1 text-sm text-slate-500">
                                            Начните с первого критерия справа. Здесь сразу появится удобная таблица для работы со шкалой.
                                        </p>
                                    </div>
                                ) : (
                                    <div className="overflow-x-auto">
                                        <table className="w-full min-w-[760px]">
                                            <thead className="bg-slate-50">
                                                <tr className="border-b border-slate-200">
                                                    <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Критерий</th>
                                                    <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Тип</th>
                                                    <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Вес</th>
                                                    <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Недочет</th>
                                                    <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Описание</th>
                                                    <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Действия</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {selectedDirection.criteria.map((criterion, index) => (
                                                    <tr key={`${criterion.name}-${index}`} className="border-b border-slate-100 last:border-b-0 hover:bg-slate-50/70 transition">
                                                        <td className="px-5 py-4 align-top">
                                                            <div className="font-medium text-slate-900">{criterion.name}</div>
                                                        </td>
                                                        <td className="px-5 py-4 align-top">
                                                            <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold ${
                                                                criterion.isCritical
                                                                    ? 'bg-red-50 text-red-700'
                                                                    : 'bg-emerald-50 text-emerald-700'
                                                            }`}>
                                                                <FaIcon className={criterion.isCritical ? 'fas fa-shield-halved' : 'fas fa-scale-balanced'} />
                                                                {criterion.isCritical ? 'Критический' : 'Обычный'}
                                                            </span>
                                                        </td>
                                                        <td className="px-5 py-4 align-top">
                                                            <span className={`font-semibold ${criterion.isCritical ? 'text-slate-500' : 'text-slate-900'}`}>
                                                                {criterion.isCritical ? '0' : `${criterion.weight}%`}
                                                            </span>
                                                        </td>
                                                        <td className="px-5 py-4 align-top">
                                                            {criterion.deficiency ? (
                                                                <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-amber-50 text-amber-700 text-xs font-semibold">
                                                                    <FaIcon className="fas fa-minus-circle" />
                                                                    {criterion.deficiency.weight}%
                                                                </span>
                                                            ) : (
                                                                <span className="text-xs text-slate-400">Нет</span>
                                                            )}
                                                        </td>
                                                        <td className="px-5 py-4 align-top">
                                                            <div className="text-sm text-slate-600 whitespace-pre-wrap break-words max-w-[420px]">
                                                                {criterion.value || 'Нет описания'}
                                                            </div>
                                                            {criterion.deficiency?.description && (
                                                                <div className="mt-2 text-xs text-amber-700 whitespace-pre-wrap break-words max-w-[420px]">
                                                                    Недочет: {criterion.deficiency.description}
                                                                </div>
                                                            )}
                                                        </td>
                                                        <td className="px-5 py-4 align-top">
                                                            <div className="flex items-center justify-end gap-2">
                                                                <button
                                                                    type="button"
                                                                    onClick={() => handleEditCriterion(criterion, index)}
                                                                    disabled={!canEdit}
                                                                    className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 transition disabled:opacity-50 disabled:cursor-not-allowed"
                                                                >
                                                                    <FaIcon className="fas fa-pen" />
                                                                    Править
                                                                </button>
                                                                <button
                                                                    type="button"
                                                                    onClick={() => handleDeleteCriterion(index)}
                                                                    disabled={!canEdit}
                                                                    className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-red-200 bg-red-50 text-red-700 hover:bg-red-100 transition disabled:opacity-50 disabled:cursor-not-allowed"
                                                                >
                                                                    <FaIcon className="fas fa-trash" />
                                                                    Удалить
                                                                </button>
                                                            </div>
                                                        </td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </div>

                            <div className="rounded-2xl border border-slate-200 bg-slate-50 px-5 py-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                <div className="text-sm text-slate-600">
                                    {hasUnsavedChanges
                                        ? 'Есть несохраненные изменения в шкале. После сохранения список обновится с актуальными id от сервера.'
                                        : 'Изменений нет. Шкала синхронизирована с сервером.'}
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <button
                                        type="button"
                                        onClick={() => onRefresh?.()}
                                        className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 transition"
                                    >
                                        <FaIcon className="fas fa-rotate-right" />
                                        Обновить данные
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => { void handleSave(); }}
                                        disabled={!canEdit || !hasUnsavedChanges || loading}
                                        className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
                                    >
                                        <FaIcon className="fas fa-save" />
                                        Сохранить изменения
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                </section>
            </div>
        </div>
    );
};

export default MonitoringScaleView;
