import React, { useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';

const FILTER_ITEMS = [
    { id: 'all', label: 'Все' },
    { id: 'files', label: 'С файлами' },
    { id: 'issues', label: 'С проблемами' }
];

const CRITERION_FLOW_STEPS = [
    {
        id: 'type',
        label: 'Тип критерия',
        description: 'Выберите модель оценки'
    },
    {
        id: 'content',
        label: 'Основа',
        description: 'Название и описание'
    },
    {
        id: 'scoring',
        label: 'Оценка',
        description: 'Вес и недочет'
    },
    {
        id: 'review',
        label: 'Проверка',
        description: 'Финальный просмотр'
    }
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
    const criterionBuilderRef = useRef(null);
    const sourceDirections = useMemo(() => normalizeDirections(directions), [directions]);
    const [draftDirections, setDraftDirections] = useState(sourceDirections);
    const [selectedDirectionId, setSelectedDirectionId] = useState(sourceDirections[0]?._localId || '');
    const [searchQuery, setSearchQuery] = useState('');
    const [activeFilter, setActiveFilter] = useState('all');
    const [criterionDraft, setCriterionDraft] = useState(createEmptyCriterionDraft());
    const [editingCriterionIndex, setEditingCriterionIndex] = useState(null);
    const [criterionStep, setCriterionStep] = useState(0);
    const [isCriterionFlowOpen, setIsCriterionFlowOpen] = useState(false);

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
        setCriterionStep(0);
        setIsCriterionFlowOpen(false);
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
        setCriterionStep(0);
    };

    const focusCriterionBuilder = () => {
        if (typeof window === 'undefined') return;
        window.requestAnimationFrame(() => {
            criterionBuilderRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    };

    const openCriterionFlow = () => {
        setIsCriterionFlowOpen(true);
        focusCriterionBuilder();
    };

    const closeCriterionFlow = () => {
        setIsCriterionFlowOpen(false);
        resetCriterionEditor();
    };

    const handleStartCriterion = () => {
        if (!selectedDirection) {
            notify('Сначала выберите направление.', 'error');
            return;
        }

        resetCriterionEditor();
        openCriterionFlow();
    };

    const getCriterionDraftError = (scope = 'all') => {
        if (!selectedDirection) {
            return 'Сначала выберите направление.';
        }

        const name = criterionDraft.name.trim();
        const requiresContentValidation = scope === 'content' || scope === 'all';
        const requiresScoringValidation = scope === 'scoring' || scope === 'all';

        if (requiresContentValidation && !name) {
            return 'Введите название критерия.';
        }

        const baseCriterion = editingCriterionIndex != null
            ? selectedDirection.criteria[editingCriterionIndex]
            : null;
        const reservedWeight = baseCriterion && !baseCriterion.isCritical ? Number(baseCriterion.weight || 0) : 0;
        const currentWeightedTotal = getWeightedTotal(selectedDirection) - reservedWeight;

        let normalizedWeight = 0;
        if (requiresScoringValidation && !criterionDraft.isCritical) {
            normalizedWeight = Number(criterionDraft.weight);
            if (!Number.isFinite(normalizedWeight) || normalizedWeight <= 0) {
                return 'Вес критерия должен быть больше 0.';
            }
            if (currentWeightedTotal + normalizedWeight > 100) {
                return `Сумма весов в направлении не может превышать 100. Доступно: ${Math.max(0, 100 - currentWeightedTotal)}%.`;
            }
        }

        let deficiency = null;
        if (requiresScoringValidation && !criterionDraft.isCritical && criterionDraft.hasDeficiency) {
            const deficiencyWeight = Number(criterionDraft.deficiencyWeight);
            if (!Number.isFinite(deficiencyWeight) || deficiencyWeight <= 0) {
                return 'Вес недочета должен быть больше 0.';
            }
            if (deficiencyWeight > normalizedWeight) {
                return 'Вес недочета не может быть больше веса критерия.';
            }
            deficiency = {
                weight: deficiencyWeight,
                description: criterionDraft.deficiencyDescription.trim() || 'Нет описания'
            };
        }

        if (scope !== 'all') {
            return '';
        }

        return {
            name,
            weight: criterionDraft.isCritical ? 0 : normalizedWeight,
            isCritical: criterionDraft.isCritical,
            value: criterionDraft.value.trim() || 'Нет описания',
            deficiency
        };
    };

    const validateCriterionDraft = () => {
        const validationResult = getCriterionDraftError('all');
        if (typeof validationResult === 'string') {
            notify(validationResult, 'error');
            return null;
        }

        return validationResult;
    };

    const handleNextCriterionStep = () => {
        const scopeByStep = {
            1: 'content',
            2: 'scoring'
        };
        const scope = scopeByStep[criterionStep];
        if (scope) {
            const error = getCriterionDraftError(scope);
            if (error) {
                notify(error, 'error');
                return;
            }
        }

        setCriterionStep((prev) => Math.min(prev + 1, CRITERION_FLOW_STEPS.length - 1));
    };

    const handlePreviousCriterionStep = () => {
        setCriterionStep((prev) => Math.max(prev - 1, 0));
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

        notify(editingCriterionIndex != null ? 'Критерий обновлен.' : 'Критерий добавлен.', 'success');
        closeCriterionFlow();
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
        setCriterionStep(0);
        openCriterionFlow();
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
            closeCriterionFlow();
        } else if (editingCriterionIndex != null && index < editingCriterionIndex) {
            setEditingCriterionIndex((prev) => (prev == null ? prev : prev - 1));
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

    const projectedWeightedTotal = useMemo(() => {
        if (!selectedDirection) return 0;

        const originalCriterion = editingCriterionIndex != null
            ? selectedDirection.criteria[editingCriterionIndex]
            : null;
        const reservedWeight = originalCriterion && !originalCriterion.isCritical ? Number(originalCriterion.weight || 0) : 0;
        const currentWeightedTotal = getWeightedTotal(selectedDirection) - reservedWeight;
        const draftWeight = criterionDraft.isCritical ? 0 : Number(criterionDraft.weight || 0);
        const normalizedDraftWeight = Number.isFinite(draftWeight) && draftWeight > 0 ? draftWeight : 0;

        return currentWeightedTotal + normalizedDraftWeight;
    }, [criterionDraft.isCritical, criterionDraft.weight, editingCriterionIndex, selectedDirection]);

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

                            <div className="space-y-6">
                                <div className="rounded-2xl border border-slate-200 overflow-hidden">
                                    <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex items-center justify-between gap-3">
                                        <div>
                                            <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-blue-100 text-blue-700 text-[11px] font-semibold uppercase tracking-[0.18em]">
                                                Шаг 1
                                            </div>
                                            <h3 className="mt-3 text-sm font-semibold text-slate-900">Параметры направления</h3>
                                            <p className="text-xs text-slate-500 mt-0.5">
                                                Сначала настройте направление, а затем переходите к созданию критериев.
                                            </p>
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
                                        <div className="rounded-2xl border border-blue-100 bg-blue-50 px-4 py-4 text-sm text-blue-900">
                                            Основа направления задается отдельно, чтобы мастер критерия не перегружал экран лишними полями.
                                        </div>

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
                                    <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                        <div>
                                            <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-violet-100 text-violet-700 text-[11px] font-semibold uppercase tracking-[0.18em]">
                                                Шаг 2
                                            </div>
                                            <h3 className="mt-3 text-sm font-semibold text-slate-900">
                                                {editingCriterionIndex != null ? 'Мастер редактирования критерия' : 'Мастер создания критерия'}
                                            </h3>
                                            <p className="text-xs text-slate-500 mt-0.5">
                                                Вместо длинной формы мастер показывает только текущий этап: тип, основу, оценку и проверку.
                                            </p>
                                        </div>
                                        <div className="flex flex-wrap gap-2">
                                            <span className="inline-flex items-center gap-2 px-3 py-2 rounded-xl bg-white border border-slate-200 text-sm text-slate-600">
                                                <FaIcon className="fas fa-diagram-project" />
                                                Шаг {criterionStep + 1} из {CRITERION_FLOW_STEPS.length}
                                            </span>
                                            {isCriterionFlowOpen ? (
                                                <button
                                                    type="button"
                                                    onClick={closeCriterionFlow}
                                                    className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 transition"
                                                >
                                                    <FaIcon className="fas fa-xmark" />
                                                    Закрыть мастер
                                                </button>
                                            ) : (
                                                <button
                                                    type="button"
                                                    onClick={handleStartCriterion}
                                                    disabled={!canEdit}
                                                    className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
                                                >
                                                    <FaIcon className="fas fa-plus" />
                                                    Добавить критерий
                                                </button>
                                            )}
                                        </div>
                                    </div>

                                    {!isCriterionFlowOpen ? (
                                        <div className="p-5">
                                            <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-5 py-5">
                                                <div className="text-sm font-semibold text-slate-900">Создание критерия по шагам</div>
                                                <p className="mt-2 text-sm text-slate-500">
                                                    Откройте мастер, чтобы последовательно пройти тип, описание, оценку и финальную проверку.
                                                </p>
                                                <div className="mt-4 grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
                                                    {CRITERION_FLOW_STEPS.map((step, index) => (
                                                        <div key={step.id} className="rounded-xl border border-white bg-white px-3 py-3">
                                                            <div className="font-semibold text-slate-900">{index + 1}. {step.label}</div>
                                                            <div className="mt-1 text-slate-500">{step.description}</div>
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        </div>
                                    ) : (
                                        <div className="p-5 space-y-5">
                                            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                                                {CRITERION_FLOW_STEPS.map((step, index) => (
                                                    <button
                                                        key={step.id}
                                                        type="button"
                                                        onClick={() => index < criterionStep && setCriterionStep(index)}
                                                        disabled={index > criterionStep}
                                                        className={`rounded-2xl border px-3 py-3 text-left transition ${
                                                            index === criterionStep ? 'border-blue-300 bg-blue-50' : index < criterionStep ? 'border-emerald-200 bg-emerald-50/70' : 'border-slate-200 bg-slate-50 text-slate-400'
                                                        }`}
                                                    >
                                                        <div className="text-xs font-semibold">{index + 1}. {step.label}</div>
                                                        <div className="mt-1 text-[11px]">{step.description}</div>
                                                    </button>
                                                ))}
                                            </div>

                                            {criterionStep === 0 && (
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                    <button type="button" onClick={() => setCriterionDraft((prev) => ({ ...prev, isCritical: false }))} className={`rounded-2xl border p-4 text-left transition ${!criterionDraft.isCritical ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'}`}>
                                                        <div className="text-sm font-semibold text-slate-900">Обычный критерий</div>
                                                        <div className="mt-1 text-sm text-slate-500">Использует вес и может иметь недочет.</div>
                                                    </button>
                                                    <button type="button" onClick={() => setCriterionDraft((prev) => ({ ...prev, isCritical: true, weight: '', hasDeficiency: false, deficiencyWeight: '', deficiencyDescription: '' }))} className={`rounded-2xl border p-4 text-left transition ${criterionDraft.isCritical ? 'border-red-300 bg-red-50' : 'border-slate-200 bg-white'}`}>
                                                        <div className="text-sm font-semibold text-slate-900">Критический критерий</div>
                                                        <div className="mt-1 text-sm text-slate-500">Обнуляет результат и не использует вес.</div>
                                                    </button>
                                                </div>
                                            )}

                                            {criterionStep === 1 && (
                                                <div className="space-y-4">
                                                    <div>
                                                        <label className="block text-sm font-medium text-slate-700 mb-2">Название критерия</label>
                                                        <input type="text" value={criterionDraft.name} onChange={(event) => setCriterionDraft((prev) => ({ ...prev, name: event.target.value }))} disabled={!canEdit} placeholder="Например, Проверил потребность" className="w-full px-4 py-3 rounded-xl border border-slate-200 bg-white text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-50" />
                                                    </div>
                                                    <div>
                                                        <label className="block text-sm font-medium text-slate-700 mb-2">Описание критерия</label>
                                                        <textarea rows={4} value={criterionDraft.value} onChange={(event) => setCriterionDraft((prev) => ({ ...prev, value: event.target.value }))} disabled={!canEdit} placeholder="Подробно опишите, как должен оцениваться критерий." className="w-full px-4 py-3 rounded-xl border border-slate-200 bg-white text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 resize-y disabled:bg-slate-50" />
                                                    </div>
                                                </div>
                                            )}

                                            {criterionStep === 2 && (
                                                <div className="space-y-4">
                                                    {!criterionDraft.isCritical ? (
                                                        <>
                                                            <div className="flex flex-wrap items-end gap-3">
                                                                <div className="w-full max-w-xs">
                                                                    <label className="block text-sm font-medium text-slate-700 mb-2">Вес критерия</label>
                                                                    <input type="number" value={criterionDraft.weight} onChange={(event) => setCriterionDraft((prev) => ({ ...prev, weight: event.target.value }))} disabled={!canEdit} min="1" placeholder="Введите вес" className="w-full px-4 py-3 rounded-xl border border-slate-200 bg-white text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-50" />
                                                                </div>
                                                                <button type="button" onClick={() => setCriterionDraft((prev) => ({ ...prev, weight: String(remainingWeight || '') }))} disabled={!canEdit || remainingWeight <= 0} className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 transition disabled:opacity-50 disabled:cursor-not-allowed">
                                                                    <FaIcon className="fas fa-fill-drip" />
                                                                    Использовать остаток {remainingWeight}%
                                                                </button>
                                                            </div>
                                                            <label className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 cursor-pointer">
                                                                <input type="checkbox" checked={criterionDraft.hasDeficiency} onChange={(event) => setCriterionDraft((prev) => ({ ...prev, hasDeficiency: event.target.checked, deficiencyWeight: event.target.checked ? prev.deficiencyWeight : '', deficiencyDescription: event.target.checked ? prev.deficiencyDescription : '' }))} disabled={!canEdit} className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                                                                <span><span className="block text-sm font-medium text-slate-800">Добавить недочет</span><span className="block text-xs text-slate-500 mt-1">Для мягкой ошибки с меньшим штрафом.</span></span>
                                                            </label>
                                                            {criterionDraft.hasDeficiency && (
                                                                <div className="grid grid-cols-1 lg:grid-cols-[180px_minmax(0,1fr)] gap-4 rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
                                                                    <div>
                                                                        <label className="block text-sm font-medium text-amber-900 mb-2">Вес недочета</label>
                                                                        <input type="number" min="1" value={criterionDraft.deficiencyWeight} onChange={(event) => setCriterionDraft((prev) => ({ ...prev, deficiencyWeight: event.target.value }))} disabled={!canEdit} className="w-full px-4 py-3 rounded-xl border border-amber-200 bg-white text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-amber-400" />
                                                                    </div>
                                                                    <div>
                                                                        <label className="block text-sm font-medium text-amber-900 mb-2">Описание недочета</label>
                                                                        <textarea rows={3} value={criterionDraft.deficiencyDescription} onChange={(event) => setCriterionDraft((prev) => ({ ...prev, deficiencyDescription: event.target.value }))} disabled={!canEdit} placeholder="Когда ставится недочет и чем он отличается от полной ошибки." className="w-full px-4 py-3 rounded-xl border border-amber-200 bg-white text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-amber-400 resize-y" />
                                                                    </div>
                                                                </div>
                                                            )}
                                                        </>
                                                    ) : (
                                                        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-900">
                                                            Для критического критерия вес и недочет не используются.
                                                        </div>
                                                    )}
                                                </div>
                                            )}

                                            {criterionStep === 3 && (
                                                <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-4">
                                                    <div className="text-sm font-semibold text-slate-900">{criterionDraft.name.trim() || 'Название не заполнено'}</div>
                                                    <div className="mt-2 text-sm text-slate-600 whitespace-pre-wrap">{criterionDraft.value.trim() || 'После сохранения будет подставлено стандартное описание.'}</div>
                                                    <div className="mt-4 flex flex-wrap gap-2 text-xs">
                                                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-white border border-slate-200 text-slate-700">{criterionDraft.isCritical ? 'Критический' : `Вес ${criterionDraft.weight || '—'}%`}</span>
                                                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-white border border-slate-200 text-slate-700">После сохранения: {projectedWeightedTotal}/100</span>
                                                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-white border border-slate-200 text-slate-700">Остаток: {Math.max(0, 100 - projectedWeightedTotal)}%</span>
                                                    </div>
                                                </div>
                                            )}

                                            <div className="flex flex-wrap gap-3">
                                                {criterionStep > 0 && (
                                                    <button type="button" onClick={handlePreviousCriterionStep} className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 transition">
                                                        <FaIcon className="fas fa-arrow-left" />
                                                        Назад
                                                    </button>
                                                )}
                                                {criterionStep < CRITERION_FLOW_STEPS.length - 1 ? (
                                                    <button type="button" onClick={handleNextCriterionStep} disabled={!canEdit} className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed">
                                                        Далее
                                                        <FaIcon className="fas fa-arrow-right" />
                                                    </button>
                                                ) : (
                                                    <button type="button" onClick={handleSubmitCriterion} disabled={!canEdit} className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed">
                                                        <FaIcon className={editingCriterionIndex != null ? 'fas fa-floppy-disk' : 'fas fa-plus'} />
                                                        {editingCriterionIndex != null ? 'Сохранить изменения' : 'Добавить критерий'}
                                                    </button>
                                                )}
                                                <div className="inline-flex items-center gap-2 px-3 py-2 rounded-xl bg-slate-100 text-slate-600 text-sm">
                                                    <FaIcon className="fas fa-circle-info" />
                                                    Свободный вес: {remainingWeight}%
                                                </div>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            </div>

                            <div className="rounded-2xl border border-slate-200 overflow-hidden">
                                <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                    <div>
                                        <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-emerald-100 text-emerald-700 text-[11px] font-semibold uppercase tracking-[0.18em]">
                                            Шаг 3
                                        </div>
                                        <h3 className="mt-3 text-sm font-semibold text-slate-900">Критерии направления</h3>
                                        <p className="text-xs text-slate-500 mt-0.5">
                                            Здесь остается только обзор. Создание и редактирование теперь вынесены в мастер выше.
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
                                        {!isCriterionFlowOpen && (
                                            <button
                                                type="button"
                                                onClick={handleStartCriterion}
                                                disabled={!canEdit}
                                                className="inline-flex items-center gap-2 px-3 py-2 rounded-xl bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
                                            >
                                                <FaIcon className="fas fa-plus" />
                                                Новый критерий
                                            </button>
                                        )}
                                    </div>
                                </div>

                                {selectedDirection.criteria.length === 0 ? (
                                    <div className="px-6 py-14 text-center">
                                        <div className="w-14 h-14 mx-auto rounded-2xl bg-slate-100 text-slate-400 flex items-center justify-center">
                                            <FaIcon className="fas fa-list-check" />
                                        </div>
                                        <h4 className="mt-4 text-sm font-semibold text-slate-900">Критерии еще не добавлены</h4>
                                        <p className="mt-1 text-sm text-slate-500">
                                            Откройте мастер выше и создайте первый критерий по шагам. После сохранения он появится в этом списке.
                                        </p>
                                        {!isCriterionFlowOpen && (
                                            <button
                                                type="button"
                                                onClick={handleStartCriterion}
                                                disabled={!canEdit}
                                                className="mt-5 inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
                                            >
                                                <FaIcon className="fas fa-plus" />
                                                Создать первый критерий
                                            </button>
                                        )}
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
                                                                    В мастер
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
