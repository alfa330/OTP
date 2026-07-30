import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import { normalizeRole, isAdminLikeRole, roleIsAny } from '../../utils/roles';

const QUESTION_TYPES = [
    { value: 'single', label: 'Один вариант' },
    { value: 'multiple', label: 'Несколько вариантов' },
    { value: 'rating', label: 'Рейтинг 1–5' },
    { value: 'other_only', label: 'Только "Другое"' }
];
const OTHER_ANSWER_MAX_LENGTH = 500;
const QUESTION_TYPE_OTHER_ONLY = 'other_only';

const isManagerRole = (role) => isAdminLikeRole(role) || roleIsAny(role, ['sv', 'trainer']);
const questionTypeLabel = (type) => QUESTION_TYPES.find((item) => item.value === type)?.label || type;
const parseWeeksInput = (value) => {
    if (value === '' || value == null) return null;
    const number = Number(value);
    if (!Number.isFinite(number)) return null;
    return Math.max(0, Math.floor(number));
};
const SkeletonBlock = ({ className = '' }) => (
    <div className={`sk-shimmer ${className}`} />
);

const SurveysListSkeleton = ({ count = 4 }) => (
    <div className="divide-y divide-gray-50">
        {Array.from({ length: count }).map((_, i) => (
            <div key={i} className="px-4 py-3 space-y-2">
                <SkeletonBlock className="h-4 w-3/4" />
                <SkeletonBlock className="h-3 w-1/4 rounded-full" />
            </div>
        ))}
    </div>
);

const isDismissedOperatorStatus = (value) => {
    const normalized = String(value || '').trim().toLowerCase();
    return (
        normalized === 'fired'
        || normalized === 'dismissal'
        || normalized === 'dismissed'
        || normalized === 'уволен'
        || normalized === 'уволена'
        || normalized === 'уволено'
        || normalized === 'уволены'
    );
};

const toUniqueTrimmedList = (values) => {
    const source = Array.isArray(values) ? values : [];
    const normalized = [];
    source.forEach((value) => {
        const text = String(value || '').trim();
        if (text && !normalized.includes(text)) normalized.push(text);
    });
    return normalized;
};

const parseFlexibleDate = (value) => {
    if (!value) return null;
    const text = String(value).trim();
    let match = text.match(/^(\d{2})-(\d{2})-(\d{4})$/);
    if (match) return new Date(Number(match[3]), Number(match[2]) - 1, Number(match[1]));
    match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (match) return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    const parsed = new Date(text);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const getTenureWeeks = (dateLike) => {
    const date = parseFlexibleDate(dateLike);
    if (!date) return null;
    const ms = Date.now() - date.getTime();
    if (ms < 0) return 0;
    return Math.floor(ms / (7 * 24 * 60 * 60 * 1000));
};

const tenureLabel = (weeks) => {
    if (!Number.isFinite(weeks)) return 'Стаж не указан';
    if (weeks < 1) return 'Меньше недели';
    if (weeks < 4) return `${weeks} нед.`;
    const months = Math.floor(weeks / 4);
    const rest = weeks % 4;
    return rest ? `${months} мес. ${rest} нед.` : `${months} мес.`;
};

const emptyQuestion = () => ({
    id: `q_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    text: '',
    type: 'single',
    required: true,
    allowOther: false,
    options: ['', ''],
    correctOptions: [],
    points: '1',
    partialCredit: false
});

const emptyDraft = () => ({
    title: '',
    description: '',
    isTest: false,
    directionIds: [],
    groupIds: [],
    tenureWeeksMin: '',
    tenureWeeksMax: '',
    operatorIds: [],
    questions: [emptyQuestion()],
    startsAt: '',
    endsAt: '',
    singleAttempt: true,
    affectsQuality: false
});

/* ─── Тест по расписанию ─── */

// Цветом выделен только «Активен» — это единственное состояние, в котором
// от человека что-то требуется. Ожидание и завершение нейтральны.
const TEST_STATUS_META = {
    scheduled: { label: 'Запланирован', color: 'gray', icon: 'fa-clock' },
    active: { label: 'Активен', color: 'green', icon: 'fa-play' },
    finished: { label: 'Завершён', color: 'gray', icon: 'fa-flag-checkered' }
};

const testStatusMeta = (status) => TEST_STATUS_META[String(status || '')] || null;

// datetime-local <-> ISO без сдвига часового пояса: сервер и клиент живут
// в одном времени (Asia/Almaty), поэтому «как ввели — так и сохранили».
const isoToLocalInput = (value) => {
    if (!value) return '';
    const text = String(value).replace('T', ' ');
    const match = text.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})/);
    return match ? `${match[1]}T${match[2]}:${match[3]}` : '';
};

const localInputToIso = (value) => {
    const text = String(value || '').trim();
    if (!text) return null;
    const match = text.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})/);
    return match ? `${match[1]} ${match[2]}:${match[3]}:00` : null;
};

const parseServerDateTime = (value) => {
    if (!value) return null;
    const text = String(value).replace('T', ' ');
    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})[ ](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!match) {
        const parsed = new Date(text);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }
    return new Date(
        Number(match[1]),
        Number(match[2]) - 1,
        Number(match[3]),
        Number(match[4]),
        Number(match[5]),
        Number(match[6] || 0)
    );
};

const formatCountdown = (msLeft) => {
    const totalSeconds = Math.max(0, Math.floor(msLeft / 1000));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const pad = (value) => String(value).padStart(2, '0');
    return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`;
};

const parsePointsInput = (value) => {
    const number = Number(String(value ?? '').replace(',', '.'));
    if (!Number.isFinite(number) || number <= 0) return null;
    return Math.round(number * 100) / 100;
};

const formatPoints = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return String(Math.round(number * 100) / 100);
};

/* ─── small reusable primitives ─── */

const Badge = ({ children, color = 'gray' }) => {
    const colors = {
        green: 'bg-emerald-100 text-emerald-700',
        blue: 'bg-blue-100 text-blue-700',
        amber: 'bg-amber-100 text-amber-700',
        gray: 'bg-gray-100 text-gray-500',
    };
    return (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${colors[color]}`}>
            {children}
        </span>
    );
};

const SectionTitle = ({ children }) => (
    <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-2">{children}</div>
);

const ProgressBar = ({ value, color = 'blue' }) => {
    const colors = { blue: 'bg-blue-500', amber: 'bg-amber-400', emerald: 'bg-emerald-500' };
    const pct = Math.max(0, Math.min(100, Number(value) || 0));
    return (
        <div className="h-1.5 w-full bg-gray-100 rounded-full overflow-hidden">
            <div className={`h-full ${colors[color]} rounded-full transition-all duration-500`} style={{ width: `${pct}%` }} />
        </div>
    );
};

const FormField = ({ label, children }) => (
    <div className="space-y-1">
        {label && <label className="block text-xs font-medium text-gray-500">{label}</label>}
        {children}
    </div>
);

const inputCls = "w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition placeholder-gray-400";

/* ─── iOS / macOS styled primitives (survey builder) ─── */

const APPLE_FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif';
// Заполненное поле в стиле iOS «grouped form» — светло-серое внутри белых карточек.
const iosInput = "w-full px-3.5 py-2.5 text-[14px] rounded-xl bg-slate-100 border-0 text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70 focus:bg-white transition";
const iosCard = "rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)]";
const iosGroupLabel = "px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400";

const IosToggle = ({ checked, onChange, disabled = false }) => (
    <button
        type="button"
        role="switch"
        aria-checked={!!checked}
        disabled={disabled}
        onClick={() => { if (!disabled) onChange(!checked); }}
        className={`relative inline-flex h-[26px] w-[44px] shrink-0 items-center rounded-full transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
            checked ? 'bg-emerald-500' : 'bg-slate-300'
        } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
    >
        <span
            className={`inline-block h-[22px] w-[22px] transform rounded-full bg-white shadow-md transition-transform duration-200 ${
                checked ? 'translate-x-[20px]' : 'translate-x-[2px]'
            }`}
        />
    </button>
);

const IosSection = ({ title, hint, children, right = null }) => (
    <section className="space-y-1.5">
        {(title || right) && (
            <div className="flex items-end justify-between gap-2">
                {title ? <div className={iosGroupLabel}>{title}</div> : <span />}
                {right}
            </div>
        )}
        <div className={`${iosCard} p-4 space-y-3`}>
            {children}
        </div>
        {hint && <div className="px-1 text-[11px] text-slate-400">{hint}</div>}
    </section>
);

/* ─── main component ─── */

const SurveysView = ({ user, operators = [], directions = [], departments = [], showToast, apiBaseUrl, onSurveyProgressChanged }) => {
    const [surveys, setSurveys] = useState([]);
    const [assignableGroups, setAssignableGroups] = useState([]);
    const [selectedSurveyId, setSelectedSurveyId] = useState('');
    const [showBuilder, setShowBuilder] = useState(false);
    const [repeatSourceSurveyId, setRepeatSourceSurveyId] = useState(null);
    const [editingSurveyId, setEditingSurveyId] = useState(null);
    const [draft, setDraft] = useState(emptyDraft);
    const [operatorQuery, setOperatorQuery] = useState('');
    const [answers, setAnswers] = useState({});
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [isStatsExporting, setIsStatsExporting] = useState(false);
    const [activeTab, setActiveTab] = useState('questions'); // 'questions' | 'stats'
    const [statsViewMode, setStatsViewMode] = useState('answers'); // 'scores' | 'answers'
    const [statsOperatorQuery, setStatsOperatorQuery] = useState('');
    const [departmentFilter, setDepartmentFilter] = useState('');
    const showToastRef = useRef(showToast);
    const onSurveyProgressChangedRef = useRef(onSurveyProgressChanged);

    const canManage = isManagerRole(user?.role);
    const isOperator = normalizeRole(user?.role) === 'operator';
    const isRepeatMode = repeatSourceSurveyId != null;
    const isEditMode = editingSurveyId != null;
    const departmentOptions = useMemo(
        () => (Array.isArray(departments) ? departments : []).filter((department) => department?.id != null && department?.is_active !== false),
        [departments]
    );
    const selectedDepartmentId = useMemo(() => {
        const parsed = Number(departmentFilter);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    }, [departmentFilter]);
    const canFilterByDepartment = canManage && departmentOptions.length > 0;

    useEffect(() => { showToastRef.current = showToast; }, [showToast]);
    useEffect(() => { onSurveyProgressChangedRef.current = onSurveyProgressChanged; }, [onSurveyProgressChanged]);

    const notify = useCallback((message, type = 'success') => {
        if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
    }, []);

    const headers = useMemo(
        () => ({ 'X-User-Id': user?.id }),
        [user?.id]
    );

    useEffect(() => {
        if (!departmentFilter) return;
        const exists = departmentOptions.some((department) => String(department?.id) === String(departmentFilter));
        if (!exists) setDepartmentFilter('');
    }, [departmentFilter, departmentOptions]);

    const directionNameById = useMemo(() => {
        const map = new Map();
        (directions || []).forEach((direction) => {
            const id = direction?.id != null ? String(direction.id) : null;
            const name = direction?.name || direction?.title || direction?.direction_name || 'Без направления';
            if (id) map.set(id, name);
        });
        return map;
    }, [directions]);

    const operatorSourceRows = useMemo(() => {
        const map = new Map();
        (operators || []).forEach((operator) => {
            const id = Number(operator?.id);
            if (Number.isFinite(id)) map.set(id, operator);
        });
        (surveys || []).forEach((survey) => {
            (survey?.assignment?.operators || []).forEach((assignmentOperator) => {
                const id = Number(assignmentOperator?.operator_id ?? assignmentOperator?.id);
                if (!Number.isFinite(id) || map.has(id)) return;
                map.set(id, {
                    id,
                    name: assignmentOperator?.operator_name || `#${id}`,
                    status: assignmentOperator?.operator_status || '',
                    direction: assignmentOperator?.direction || '',
                    direction_id: assignmentOperator?.direction_id,
                    department_id: assignmentOperator?.department_id ?? assignmentOperator?.departmentId
                });
            });
        });
        return Array.from(map.values());
    }, [operators, surveys]);

    const normalizedOperators = useMemo(() => {
        return operatorSourceRows
            .map((operator) => {
                const id = Number(operator?.id);
                if (!Number.isFinite(id)) return null;
                const status = String(operator?.status || '').trim().toLowerCase();
                const statusPeriodCode = String(operator?.status_period_status_code || '').trim().toLowerCase();
                const isDismissed = isDismissedOperatorStatus(status) || isDismissedOperatorStatus(statusPeriodCode) || operator?.is_operator_dismissed === true;
                const directionId = operator?.direction_id != null ? String(operator.direction_id) : 'none';
                const departmentIdRaw = operator?.department_id ?? operator?.departmentId;
                const departmentIdNumber = Number(departmentIdRaw);
                const weeks = getTenureWeeks(operator?.hire_date);
                return {
                    id,
                    name: String(operator?.name || `#${id}`),
                    directionId,
                    departmentId: Number.isFinite(departmentIdNumber) ? departmentIdNumber : null,
                    directionName: operator?.direction || directionNameById.get(directionId) || 'Без направления',
                    tenureWeeks: weeks,
                    tenureLabel: tenureLabel(weeks),
                    isDismissed,
                    statusLabel: isDismissed ? 'Уволен' : 'Активен'
                };
            })
            .filter(Boolean)
            .sort((a, b) => a.name.localeCompare(b.name, 'ru', { sensitivity: 'base' }));
    }, [directionNameById, operatorSourceRows]);
    const availableOperatorIdSet = useMemo(
        () => new Set(normalizedOperators.map((operator) => Number(operator.id)).filter(Number.isFinite)),
        [normalizedOperators]
    );
    const operatorDepartmentIdById = useMemo(() => {
        const map = new Map();
        normalizedOperators.forEach((operator) => {
            const id = Number(operator?.id);
            const departmentId = Number(operator?.departmentId);
            if (Number.isFinite(id) && Number.isFinite(departmentId)) {
                map.set(id, departmentId);
            }
        });
        return map;
    }, [normalizedOperators]);
    const dismissedOperatorIdSet = useMemo(
        () => new Set(normalizedOperators.filter((operator) => operator.isDismissed).map((operator) => Number(operator.id))),
        [normalizedOperators]
    );
    const sanitizeOperatorIds = useCallback((sourceIds, options = {}) => {
        const excludeDismissed = !!options.excludeDismissed;
        const uniqueIds = [];
        const seen = new Set();
        (Array.isArray(sourceIds) ? sourceIds : []).forEach((rawId) => {
            const id = Number(rawId);
            if (!Number.isFinite(id) || !availableOperatorIdSet.has(id) || seen.has(id)) return;
            if (excludeDismissed && dismissedOperatorIdSet.has(id)) return;
            seen.add(id);
            uniqueIds.push(id);
        });
        return uniqueIds;
    }, [availableOperatorIdSet, dismissedOperatorIdSet]);

    useEffect(() => {
        setDraft((prev) => {
            const currentIds = Array.isArray(prev?.operatorIds) ? prev.operatorIds : [];
            const nextIds = sanitizeOperatorIds(currentIds);
            if (nextIds.length === currentIds.length && nextIds.every((id, index) => id === Number(currentIds[index]))) {
                return prev;
            }
            return { ...prev, operatorIds: nextIds };
        });
    }, [sanitizeOperatorIds]);

    // Группы — такой же фильтр состава, как направления: выбираешь группу,
    // а «Выбрать всех» назначает её операторов.
    const groupOptions = useMemo(() => {
        const rows = Array.isArray(assignableGroups) ? assignableGroups : [];
        return rows.filter((group) => (
            selectedDepartmentId == null
            || group?.department_id == null
            || Number(group.department_id) === selectedDepartmentId
        ));
    }, [assignableGroups, selectedDepartmentId]);

    const selectedGroupOperatorIds = useMemo(() => {
        const selected = new Set((draft.groupIds || []).map((id) => Number(id)).filter(Number.isFinite));
        if (selected.size === 0) return null;
        const ids = new Set();
        (Array.isArray(assignableGroups) ? assignableGroups : []).forEach((group) => {
            if (!selected.has(Number(group?.id))) return;
            (group?.operator_ids || []).forEach((operatorId) => {
                const parsed = Number(operatorId);
                if (Number.isFinite(parsed)) ids.add(parsed);
            });
        });
        return ids;
    }, [assignableGroups, draft.groupIds]);

    const filteredOperators = useMemo(() => {
        const query = operatorQuery.trim().toLowerCase();
        const selectedDirections = new Set((draft.directionIds || []).map(String));
        const minWeeks = parseWeeksInput(draft.tenureWeeksMin);
        const maxWeeks = parseWeeksInput(draft.tenureWeeksMax);
        const selectedOperatorIds = new Set((draft.operatorIds || []).map((id) => Number(id)).filter(Number.isFinite));
        return normalizedOperators.filter((operator) => {
            const isAlreadySelected = selectedOperatorIds.has(Number(operator.id));
            const byQuery = !query || operator.name.toLowerCase().includes(query) || operator.directionName.toLowerCase().includes(query);
            if (!byQuery) return false;
            const byDepartment = selectedDepartmentId == null || Number(operator.departmentId) === selectedDepartmentId;
            if (!byDepartment && !isAlreadySelected) return false;
            const byGroup = selectedGroupOperatorIds == null || selectedGroupOperatorIds.has(Number(operator.id));
            // Уже выбранные операторы всегда видны, а в режиме повтора/редактирования
            // можно добрать других действующих сотрудников вне старых фильтров.
            if (isAlreadySelected) return true;
            if (!byGroup) return false;
            if (isEditMode || isRepeatMode) return true;

            const byDirection = selectedDirections.size === 0 || selectedDirections.has(operator.directionId);
            const hasTenure = Number.isFinite(operator.tenureWeeks);
            const byMin = minWeeks == null || (hasTenure && operator.tenureWeeks >= minWeeks);
            const byMax = maxWeeks == null || (hasTenure && operator.tenureWeeks <= maxWeeks);
            return byDirection && byQuery && byMin && byMax;
        });
    }, [draft.directionIds, draft.operatorIds, draft.tenureWeeksMax, draft.tenureWeeksMin, isEditMode, isRepeatMode, normalizedOperators, operatorQuery, selectedDepartmentId, selectedGroupOperatorIds]);

    const filteredOperatorIds = useMemo(
        () => filteredOperators.map((operator) => Number(operator.id)).filter(Number.isFinite),
        [filteredOperators]
    );
    const filteredAssignableOperatorIds = useMemo(
        () => filteredOperators
            .filter((operator) => !operator.isDismissed)
            .map((operator) => Number(operator.id))
            .filter(Number.isFinite),
        [filteredOperators]
    );
    const filteredAssignableOperatorIdSet = useMemo(() => new Set(filteredAssignableOperatorIds), [filteredAssignableOperatorIds]);
    const selectedFilteredOperatorsCount = useMemo(
        () => (draft.operatorIds || []).reduce(
            (count, id) => count + (filteredAssignableOperatorIdSet.has(Number(id)) ? 1 : 0),
            0
        ),
        [draft.operatorIds, filteredAssignableOperatorIdSet]
    );
    const hasFilteredOperators = filteredAssignableOperatorIds.length > 0;
    const allFilteredOperatorsSelected = hasFilteredOperators && selectedFilteredOperatorsCount === filteredAssignableOperatorIds.length;

    const selectAllFilteredOperators = useCallback(() => {
        setDraft((prev) => {
            const nextSelected = new Set((prev.operatorIds || []).map((id) => Number(id)).filter(Number.isFinite));
            filteredAssignableOperatorIds.forEach((id) => nextSelected.add(id));
            return { ...prev, operatorIds: Array.from(nextSelected) };
        });
    }, [filteredAssignableOperatorIds]);

    const clearFilteredOperators = useCallback(() => {
        if (!hasFilteredOperators) return;
        const toRemove = new Set(filteredOperatorIds);
        setDraft((prev) => ({
            ...prev,
            operatorIds: (prev.operatorIds || []).filter((id) => !toRemove.has(Number(id)))
        }));
    }, [filteredOperatorIds, hasFilteredOperators]);

    const loadSurveys = useCallback(async () => {
        if (!apiBaseUrl || !user?.id) return;
        setIsLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/surveys`, { headers });
            setSurveys(Array.isArray(response?.data?.surveys) ? response.data.surveys : []);
            // Состав групп приходит тем же ответом — отдельный запрос не нужен.
            setAssignableGroups(Array.isArray(response?.data?.groups) ? response.data.groups : []);
            if (typeof onSurveyProgressChangedRef.current === 'function') {
                onSurveyProgressChangedRef.current();
            }
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось загрузить опросы', 'error');
        } finally {
            setIsLoading(false);
        }
    }, [apiBaseUrl, headers, notify, user?.id]);

    const assignmentMatchesSelectedDepartment = useCallback((assignment) => {
        if (selectedDepartmentId == null) return true;
        const directDepartmentId = Number(assignment?.department_id ?? assignment?.departmentId);
        if (Number.isFinite(directDepartmentId)) return directDepartmentId === selectedDepartmentId;
        const operatorId = Number(assignment?.operator_id ?? assignment?.id);
        const mappedDepartmentId = operatorDepartmentIdById.get(operatorId);
        return Number(mappedDepartmentId) === selectedDepartmentId;
    }, [operatorDepartmentIdById, selectedDepartmentId]);

    const getSurveyDisplayMetrics = useCallback((survey) => {
        const statistics = survey?.statistics || {};
        const fallbackAssigned = Number(statistics.assigned_count || 0);
        const fallbackCompleted = Number(statistics.completed_count || 0);
        const fallbackPending = Number(statistics.pending_count || Math.max(0, fallbackAssigned - fallbackCompleted));
        const fallbackRate = Number(statistics.completion_rate || 0);
        const assignments = Array.isArray(survey?.assignment?.operators) ? survey.assignment.operators : [];

        if (selectedDepartmentId == null || assignments.length === 0) {
            return {
                assignedCount: fallbackAssigned,
                completedCount: fallbackCompleted,
                pendingCount: fallbackPending,
                completionRate: Number.isFinite(fallbackRate) ? fallbackRate : 0
            };
        }

        const departmentAssignments = assignments.filter(assignmentMatchesSelectedDepartment);
        const assignedCount = departmentAssignments.length;
        const completedCount = departmentAssignments.filter(
            (assignment) => String(assignment?.status || '').trim().toLowerCase() === 'completed'
        ).length;
        const pendingCount = Math.max(0, assignedCount - completedCount);
        const completionRate = assignedCount > 0 ? Math.round((completedCount / assignedCount) * 1000) / 10 : 0;

        return { assignedCount, completedCount, pendingCount, completionRate };
    }, [assignmentMatchesSelectedDepartment, selectedDepartmentId]);

    const visibleSurveys = useMemo(() => {
        if (!canManage || selectedDepartmentId == null) return surveys;
        return (surveys || []).filter((survey) => getSurveyDisplayMetrics(survey).assignedCount > 0);
    }, [canManage, getSurveyDisplayMetrics, selectedDepartmentId, surveys]);

    useEffect(() => { loadSurveys(); }, [loadSurveys]);

    useEffect(() => {
        if (!selectedSurveyId && visibleSurveys[0]?.id) setSelectedSurveyId(visibleSurveys[0].id);
        if (selectedSurveyId && !visibleSurveys.some((item) => String(item.id) === String(selectedSurveyId))) {
            setSelectedSurveyId(visibleSurveys[0]?.id || '');
        }
    }, [selectedSurveyId, visibleSurveys]);

    useEffect(() => {
        setStatsOperatorQuery('');
        const currentSurvey = (visibleSurveys || []).find((item) => String(item.id) === String(selectedSurveyId));
        setStatsViewMode(currentSurvey?.is_test ? 'scores' : 'answers');
    }, [selectedSurveyId, visibleSurveys]);

    const selectedSurvey = useMemo(
        () => visibleSurveys.find((item) => String(item.id) === String(selectedSurveyId)) || null,
        [selectedSurveyId, visibleSurveys]
    );
    const selectedSurveyDisplayMetrics = useMemo(
        () => getSurveyDisplayMetrics(selectedSurvey),
        [getSurveyDisplayMetrics, selectedSurvey]
    );
    const isTestStatsSurvey = !!selectedSurvey?.is_test;
    const selectedSurveyQuestionMetaById = useMemo(() => {
        const map = new Map();
        (selectedSurvey?.questions || []).forEach((question, index) => {
            const questionId = Number(question?.id);
            if (!Number.isFinite(questionId)) return;
            map.set(questionId, {
                index,
                text: String(question?.text || `Вопрос ${index + 1}`),
                correctOptions: toUniqueTrimmedList(question?.correct_options)
            });
        });
        return map;
    }, [selectedSurvey?.questions]);

    const surveyQuestionsBySurveyId = useMemo(() => {
        const map = new Map();
        (surveys || []).forEach((survey) => {
            const surveyId = Number(survey?.id);
            if (!Number.isFinite(surveyId)) return;
            const questions = Array.isArray(survey?.questions)
                ? [...survey.questions].sort((a, b) => {
                    const posA = Number(a?.position) || 0;
                    const posB = Number(b?.position) || 0;
                    if (posA !== posB) return posA - posB;
                    return (Number(a?.id) || 0) - (Number(b?.id) || 0);
                })
                : [];
            map.set(surveyId, questions);
        });
        return map;
    }, [surveys]);

    const detailedStatsSourceRows = useMemo(() => {
        const allRepetitionRows = Array.isArray(selectedSurvey?.statistics?.responses_detailed_all_repetitions)
            ? selectedSurvey.statistics.responses_detailed_all_repetitions
            : [];
        if (allRepetitionRows.length > 0) return allRepetitionRows;
        return Array.isArray(selectedSurvey?.statistics?.responses_detailed)
            ? selectedSurvey.statistics.responses_detailed
            : [];
    }, [selectedSurvey?.statistics?.responses_detailed_all_repetitions, selectedSurvey?.statistics?.responses_detailed]);

    const departmentFilteredDetailedStatsRows = useMemo(() => {
        if (!canManage || selectedDepartmentId == null) return detailedStatsSourceRows;
        return detailedStatsSourceRows.filter((row) => assignmentMatchesSelectedDepartment(row));
    }, [assignmentMatchesSelectedDepartment, canManage, detailedStatsSourceRows, selectedDepartmentId]);

    const detailedStatsRows = useMemo(() => {
        const rows = departmentFilteredDetailedStatsRows;
        const query = String(statsOperatorQuery || '').trim().toLowerCase();
        if (!query) return rows;
        return rows.filter((row) => {
            const name = String(row?.operator_name || '').toLowerCase();
            const idText = String(row?.operator_id || '');
            return name.includes(query) || idText.includes(query);
        });
    }, [departmentFilteredDetailedStatsRows, statsOperatorQuery]);

    const resolveStatsQuestionAndAnswer = useCallback((row, baseQuestion, questionIndex) => {
        const rowSurveyId = Number(row?.repeat_survey_id);
        const rowQuestions = Number.isFinite(rowSurveyId)
            ? (surveyQuestionsBySurveyId.get(rowSurveyId) || [])
            : [];
        const rowQuestion = rowQuestions[questionIndex] || baseQuestion;
        const answersByQuestion = row?.answers_by_question || {};

        let resolvedAnswer = null;
        if (rowQuestion) {
            resolvedAnswer = answersByQuestion[String(rowQuestion.id)] || answersByQuestion[rowQuestion.id] || null;
        }
        if (!resolvedAnswer) {
            const answersList = Array.isArray(row?.answers) ? row.answers : [];
            if (rowQuestion) {
                resolvedAnswer = answersList.find((item) => Number(item?.question_id) === Number(rowQuestion.id)) || null;
            }
            if (!resolvedAnswer) {
                resolvedAnswer = answersList[questionIndex] || null;
            }
        }

        return {
            question: rowQuestion || baseQuestion,
            answer: resolvedAnswer
        };
    }, [surveyQuestionsBySurveyId]);

    const formatQuestionAnswerText = useCallback((question, answer) => {
        if (!question || !answer) return '—';
        if (question.type === 'rating') {
            const rating = Number(answer.rating_value);
            return Number.isFinite(rating) ? `${rating}` : '—';
        }

        const selectedOptions = Array.isArray(answer.selected_options)
            ? answer.selected_options.map((item) => String(item || '').trim()).filter(Boolean)
            : [];
        const otherText = String(answer.answer_text || '').trim();

        if (selectedOptions.length > 0 && otherText) {
            return `${selectedOptions.join(', ')}; Другое: ${otherText}`;
        }
        if (selectedOptions.length > 0) {
            return selectedOptions.join(', ');
        }
        if (otherText) {
            return `Другое: ${otherText}`;
        }
        return '—';
    }, []);

    const getExpectedOptionsForTest = useCallback((question, answer) => {
        const fromAnswer = toUniqueTrimmedList(answer?.expected_options);
        if (fromAnswer.length > 0) return fromAnswer;
        return toUniqueTrimmedList(question?.correct_options);
    }, []);

    const isTestAnswerCorrect = useCallback((question, answer) => {
        if (!question || !answer) return false;
        if (typeof answer?.is_correct === 'boolean') return answer.is_correct;

        const type = String(question?.type || '');
        const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
        const answerText = String(answer?.answer_text || '').trim();
        const expectedOptions = getExpectedOptionsForTest(question, answer);

        if (type === 'single') {
            return (
                expectedOptions.length === 1
                && selectedOptions.length === 1
                && selectedOptions[0] === expectedOptions[0]
                && !answerText
            );
        }
        if (type === 'multiple') {
            return (
                expectedOptions.length > 0
                && selectedOptions.length === expectedOptions.length
                && expectedOptions.every((option) => selectedOptions.includes(option))
                && !answerText
            );
        }
        return false;
    }, [getExpectedOptionsForTest]);

    // Частичный зачёт: сервер присылает и признак, и начисленный балл.
    const isTestAnswerPartiallyCorrect = useCallback((answer) => (
        answer?.is_partially_correct === true
        || (answer?.is_correct !== true && Number(answer?.earned_points) > 0)
    ), []);

    const testAnswerStatusMeta = useCallback((question, answer, hasAnswer) => {
        if (!hasAnswer) return { label: 'Нет ответа', color: 'gray' };
        if (isTestAnswerCorrect(question, answer)) return { label: 'Верно', color: 'green' };
        if (isTestAnswerPartiallyCorrect(answer)) return { label: 'Частично', color: 'blue' };
        return { label: 'Неверно', color: 'amber' };
    }, [isTestAnswerCorrect, isTestAnswerPartiallyCorrect]);

    const hasSurveyAnswer = useCallback((question, answer) => {
        if (!question || !answer) return false;
        if (question.type === 'rating') {
            return Number.isFinite(Number(answer?.rating_value));
        }
        const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
        const answerText = String(answer?.answer_text || '').trim();
        return selectedOptions.length > 0 || answerText.length > 0;
    }, []);

    const displayQuestionStats = useMemo(() => {
        const serverStats = Array.isArray(selectedSurvey?.statistics?.question_stats)
            ? selectedSurvey.statistics.question_stats
            : [];
        if (!canManage || selectedDepartmentId == null) return serverStats;

        const questions = Array.isArray(selectedSurvey?.questions) ? selectedSurvey.questions : [];
        const respondentsTotal = departmentFilteredDetailedStatsRows.length;

        return questions.map((question, questionIndex) => {
            const type = String(question?.type || 'single');
            const optionCounts = new Map();
            const ratingCounts = new Map();
            const ratingValues = [];
            let answeredCount = 0;
            let selectionsTotal = 0;

            departmentFilteredDetailedStatsRows.forEach((row) => {
                const resolved = resolveStatsQuestionAndAnswer(row, question, questionIndex);
                const resolvedQuestion = resolved.question || question;
                const answer = resolved.answer;
                if (!hasSurveyAnswer(resolvedQuestion, answer)) return;

                answeredCount += 1;
                if (String(resolvedQuestion?.type || type) === 'rating') {
                    const rating = Number(answer?.rating_value);
                    if (Number.isFinite(rating)) {
                        ratingValues.push(rating);
                        ratingCounts.set(rating, (ratingCounts.get(rating) || 0) + 1);
                    }
                    return;
                }

                const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
                selectedOptions.forEach((option) => {
                    optionCounts.set(option, (optionCounts.get(option) || 0) + 1);
                    selectionsTotal += 1;
                });

                const otherText = String(answer?.answer_text || '').trim();
                if (otherText) {
                    optionCounts.set('Другое', (optionCounts.get('Другое') || 0) + 1);
                    selectionsTotal += 1;
                }
            });

            const responseRate = respondentsTotal > 0 ? Math.round((answeredCount / respondentsTotal) * 1000) / 10 : 0;
            const options = Array.from(optionCounts.entries())
                .map(([option, count]) => ({
                    option,
                    count,
                    percent_of_answers: answeredCount > 0 ? (count / answeredCount) * 100 : 0,
                    percent_of_respondents: respondentsTotal > 0 ? (count / respondentsTotal) * 100 : 0
                }))
                .sort((a, b) => b.count - a.count || String(a.option).localeCompare(String(b.option), 'ru', { sensitivity: 'base' }));
            const sortedRatings = [...ratingValues].sort((a, b) => a - b);
            const medianRating = sortedRatings.length
                ? sortedRatings[Math.floor((sortedRatings.length - 1) / 2)]
                : null;
            const ratingsDistributionDetailed = [1, 2, 3, 4, 5].map((value) => {
                const count = ratingCounts.get(value) || 0;
                return {
                    value,
                    count,
                    percent_of_answers: answeredCount > 0 ? (count / answeredCount) * 100 : 0,
                    percent_of_respondents: respondentsTotal > 0 ? (count / respondentsTotal) * 100 : 0
                };
            });

            return {
                question_id: question?.id,
                text: question?.text || `Вопрос ${questionIndex + 1}`,
                type,
                correct_options: toUniqueTrimmedList(question?.correct_options),
                respondents_total: respondentsTotal,
                question_respondents_total: answeredCount,
                survey_respondents_total: respondentsTotal,
                responses_with_answer: answeredCount,
                skipped_count: Math.max(0, respondentsTotal - answeredCount),
                response_rate: responseRate,
                selections_total: selectionsTotal,
                options,
                top_options: options.slice(0, 3),
                ratings_distribution_detailed: ratingsDistributionDetailed,
                average_rating: ratingValues.length ? (ratingValues.reduce((sum, value) => sum + value, 0) / ratingValues.length).toFixed(2) : null,
                median_rating: medianRating,
                min_rating: sortedRatings.length ? sortedRatings[0] : null,
                max_rating: sortedRatings.length ? sortedRatings[sortedRatings.length - 1] : null
            };
        });
    }, [
        canManage,
        departmentFilteredDetailedStatsRows,
        hasSurveyAnswer,
        resolveStatsQuestionAndAnswer,
        selectedDepartmentId,
        selectedSurvey?.questions,
        selectedSurvey?.statistics?.question_stats
    ]);

    const formatSurveyDateTime = useCallback((value) => {
        if (!value) return '—';
        const parsed = new Date(String(value));
        if (Number.isNaN(parsed.getTime())) {
            return String(value).replace('T', ' ').slice(0, 16);
        }
        return parsed.toLocaleString('ru-RU', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    }, []);

    useEffect(() => {
        if (!isOperator || !selectedSurvey) return;
        // Черновик начатой попытки восстанавливаем: оператор мог закрыть вкладку
        // и вернуться — выбранные ответы должны остаться на месте.
        const draftByQuestion = new Map();
        (selectedSurvey?.my_assignment?.draft_answers || []).forEach((answer) => {
            const questionId = Number(answer?.question_id);
            if (!Number.isFinite(questionId)) return;
            draftByQuestion.set(questionId, Array.isArray(answer?.selected_options)
                ? answer.selected_options.map((item) => String(item || '')).filter(Boolean)
                : []);
        });
        const initial = {};
        (selectedSurvey.questions || []).forEach((question) => {
            initial[question.id] = {
                selected_options: draftByQuestion.get(Number(question.id)) || [],
                answer_text: '',
                rating_value: ''
            };
        });
        setAnswers(initial);
    }, [isOperator, selectedSurveyId, selectedSurvey]);

    /* ─── Тест по расписанию: часы, таймер и автосохранение попытки ─── */

    const selectedTestInfo = selectedSurvey?.is_test ? (selectedSurvey?.test || null) : null;
    const testEndsAtDate = useMemo(
        () => parseServerDateTime(selectedTestInfo?.ends_at),
        [selectedTestInfo?.ends_at]
    );
    const testStartsAtDate = useMemo(
        () => parseServerDateTime(selectedTestInfo?.starts_at),
        [selectedTestInfo?.starts_at]
    );
    const needsTestClock = !!selectedTestInfo && (!!testEndsAtDate || !!testStartsAtDate);
    const [testNowTs, setTestNowTs] = useState(() => Date.now());

    useEffect(() => {
        if (!needsTestClock) return undefined;
        setTestNowTs(Date.now());
        const timerId = window.setInterval(() => setTestNowTs(Date.now()), 1000);
        return () => window.clearInterval(timerId);
    }, [needsTestClock, selectedSurveyId]);

    // Статус пересчитываем от текущего времени, а не только от ответа сервера:
    // окно может закрыться, пока страница открыта.
    const liveTestStatus = useMemo(() => {
        if (!selectedTestInfo) return null;
        if (testStartsAtDate && testNowTs < testStartsAtDate.getTime()) return 'scheduled';
        if (testEndsAtDate && testNowTs >= testEndsAtDate.getTime()) return 'finished';
        return selectedTestInfo.status || 'active';
    }, [selectedTestInfo, testEndsAtDate, testNowTs, testStartsAtDate]);

    const testMsLeft = useMemo(() => {
        if (!testEndsAtDate) return null;
        return Math.max(0, testEndsAtDate.getTime() - testNowTs);
    }, [testEndsAtDate, testNowTs]);

    const isTestTimeOver = !!selectedTestInfo && liveTestStatus === 'finished';
    // Повтор теста доступен, только если он разрешён настройкой: тогда сначала
    // показываем результат прошлой попытки, а форму — по явной кнопке.
    const [retakeSurveyId, setRetakeSurveyId] = useState(null);
    useEffect(() => { setRetakeSurveyId(null); }, [selectedSurveyId]);

    const isSelectedSurveyCompleted = selectedSurvey?.my_assignment?.status === 'completed';
    const canRetakeSelectedTest = (
        !!selectedTestInfo
        && isSelectedSurveyCompleted
        && !!selectedSurvey?.my_assignment?.can_submit
        && !isTestTimeOver
    );
    const isRetakingSelectedTest = canRetakeSelectedTest && String(retakeSurveyId) === String(selectedSurveyId);
    const canFillSelectedSurvey = (
        !!selectedSurvey?.my_assignment?.can_submit
        && !isTestTimeOver
        && (!isSelectedSurveyCompleted || isRetakingSelectedTest)
    );

    const attemptSaveTimerRef = useRef(null);
    const attemptSaveSurveyIdRef = useRef(null);

    // Черновик пишем с задержкой: каждый клик по варианту — не повод
    // для запроса, а по окончании окна сервер отправит последнее сохранённое.
    const scheduleAttemptDraftSave = useCallback((surveyId, answersByQuestion) => {
        if (!apiBaseUrl || !surveyId) return;
        attemptSaveSurveyIdRef.current = surveyId;
        if (attemptSaveTimerRef.current) window.clearTimeout(attemptSaveTimerRef.current);
        attemptSaveTimerRef.current = window.setTimeout(() => {
            const payload = Object.entries(answersByQuestion || {}).map(([questionId, answer]) => ({
                question_id: Number(questionId),
                selected_options: Array.isArray(answer?.selected_options)
                    ? answer.selected_options.map((item) => String(item || '')).filter(Boolean)
                    : []
            })).filter((item) => Number.isFinite(item.question_id));
            axios.put(
                `${apiBaseUrl}/api/surveys/${surveyId}/attempt`,
                { answers: payload },
                { headers }
            ).catch(() => { /* черновик не критичен: отправку решает кнопка или окно теста */ });
        }, 1200);
    }, [apiBaseUrl, headers]);

    useEffect(() => () => {
        if (attemptSaveTimerRef.current) window.clearTimeout(attemptSaveTimerRef.current);
    }, []);

    const toggleArrayValue = (setter, key, value) => {
        setter((prev) => {
            const set = new Set(prev[key] || []);
            if (set.has(value)) set.delete(value);
            else set.add(value);
            return { ...prev, [key]: Array.from(set) };
        });
    };

    const updateQuestion = (questionId, patch) => {
        setDraft((prev) => ({ ...prev, questions: prev.questions.map((q) => (q.id === questionId ? { ...q, ...patch } : q)) }));
    };

    const addQuestionOption = (questionId) => {
        setDraft((prev) => ({
            ...prev,
            questions: prev.questions.map((question) => {
                if (question.id !== questionId || question.type === 'rating' || question.type === QUESTION_TYPE_OTHER_ONLY) return question;
                const options = Array.isArray(question.options) ? question.options : [];
                return { ...question, options: [...options, ''] };
            })
        }));
    };

    const removeQuestionOption = (questionId, optionIndex) => {
        setDraft((prev) => ({
            ...prev,
            questions: prev.questions.map((question) => {
                if (question.id !== questionId || question.type === 'rating' || question.type === QUESTION_TYPE_OTHER_ONLY) return question;
                const options = Array.isArray(question.options) ? question.options : [];
                if (options.length <= 2) return question;
                const removedOption = String(options[optionIndex] || '').trim();
                const nextCorrectOptions = toUniqueTrimmedList(
                    (question.correctOptions || []).filter((option) => String(option || '').trim() !== removedOption)
                );
                return {
                    ...question,
                    options: options.filter((_, idx) => idx !== optionIndex),
                    correctOptions: nextCorrectOptions
                };
            })
        }));
    };

    const toggleTestMode = (enabled) => {
        const nextEnabled = !!enabled;
        setDraft((prev) => ({
            ...prev,
            isTest: nextEnabled,
            questions: (prev.questions || []).map((question) => {
                let nextType = question.type;
                let nextOptions = Array.isArray(question.options) ? question.options : [];
                let nextAllowOther = !!question.allowOther;
                let nextCorrectOptions = toUniqueTrimmedList(question.correctOptions);

                if (nextEnabled) {
                    if (nextType === 'rating' || nextType === QUESTION_TYPE_OTHER_ONLY) {
                        nextType = 'single';
                        nextOptions = nextOptions.length ? nextOptions : ['', ''];
                    }
                    nextAllowOther = false;

                    const normalizedOptions = toUniqueTrimmedList(nextOptions);
                    nextCorrectOptions = nextCorrectOptions.filter((option) => normalizedOptions.includes(option));
                    if (nextType === 'single' && nextCorrectOptions.length > 1) {
                        nextCorrectOptions = [nextCorrectOptions[0]];
                    }
                }

                return {
                    ...question,
                    type: nextType,
                    allowOther: nextType === QUESTION_TYPE_OTHER_ONLY ? true : nextAllowOther,
                    options: (nextType === 'rating' || nextType === QUESTION_TYPE_OTHER_ONLY)
                        ? []
                        : (nextOptions.length ? nextOptions : ['', '']),
                    correctOptions: nextType === QUESTION_TYPE_OTHER_ONLY ? [] : nextCorrectOptions
                };
            })
        }));
    };

    const toggleCorrectOption = (questionId, optionValue) => {
        const normalizedValue = String(optionValue || '').trim();
        if (!normalizedValue) return;

        setDraft((prev) => ({
            ...prev,
            questions: (prev.questions || []).map((question) => {
                if (question.id !== questionId || question.type === 'rating' || question.type === QUESTION_TYPE_OTHER_ONLY) return question;

                const options = toUniqueTrimmedList(question.options);
                if (!options.includes(normalizedValue)) return question;

                const currentCorrectOptions = toUniqueTrimmedList(question.correctOptions);
                const hasValue = currentCorrectOptions.includes(normalizedValue);
                let nextCorrectOptions;

                if (question.type === 'single') {
                    nextCorrectOptions = hasValue ? [] : [normalizedValue];
                } else {
                    nextCorrectOptions = hasValue
                        ? currentCorrectOptions.filter((option) => option !== normalizedValue)
                        : [...currentCorrectOptions, normalizedValue];
                }

                return { ...question, correctOptions: nextCorrectOptions };
            })
        }));
    };

    const updateAnswer = (questionId, patch) => {
        setAnswers((prev) => {
            const next = { ...prev, [questionId]: { ...(prev[questionId] || {}), ...patch } };
            if (selectedSurvey?.is_test && liveTestStatus === 'active') {
                scheduleAttemptDraftSave(selectedSurvey.id, next);
            }
            return next;
        });
    };

    const resetBuilder = useCallback(() => {
        setRepeatSourceSurveyId(null);
        setEditingSurveyId(null);
        setDraft(emptyDraft());
        setOperatorQuery('');
    }, []);

    const closeBuilder = useCallback(() => {
        setShowBuilder(false);
        resetBuilder();
    }, [resetBuilder]);

    // Блокируем прокрутку фона и закрываем модалку по Escape, пока открыт конструктор.
    useEffect(() => {
        if (!showBuilder) return undefined;
        const onKeyDown = (event) => {
            if (event.key === 'Escape' && !isSaving) closeBuilder();
        };
        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        window.addEventListener('keydown', onKeyDown);
        return () => {
            document.body.style.overflow = previousOverflow;
            window.removeEventListener('keydown', onKeyDown);
        };
    }, [showBuilder, isSaving, closeBuilder]);

    const startRepeatSurvey = useCallback((survey) => {
        if (!survey || !canManage) return;
        const sourceId = Number(survey?.id);
        if (!Number.isFinite(sourceId)) return;

        const sourceQuestions = Array.isArray(survey?.questions) ? survey.questions : [];
        const clonedQuestions = sourceQuestions.length > 0
            ? sourceQuestions.map((question) => {
                const rawType = String(question?.type || 'single');
                const isOtherOnlyQuestion = (
                    rawType === 'single'
                    && survey?.is_test !== true
                    && question?.allow_other === true
                    && (!Array.isArray(question?.options) || question.options.length === 0)
                );
                const type = isOtherOnlyQuestion
                    ? QUESTION_TYPE_OTHER_ONLY
                    : (survey?.is_test && rawType === 'rating' ? 'single' : rawType);
                return {
                    id: `q_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
                    text: String(question?.text || ''),
                    type,
                    required: question?.required !== false,
                    allowOther: type === QUESTION_TYPE_OTHER_ONLY ? true : (survey?.is_test ? false : (question?.allow_other === true)),
                    options: (type === 'rating' || type === QUESTION_TYPE_OTHER_ONLY)
                        ? []
                        : (Array.isArray(question?.options) ? question.options.map((option) => String(option || '')) : ['', '']),
                    correctOptions: type === QUESTION_TYPE_OTHER_ONLY ? [] : toUniqueTrimmedList(question?.correct_options),
                    points: question?.points != null ? String(question.points) : '1',
                    partialCredit: question?.partial_credit === true && type === 'multiple'
                };
            })
            : [emptyQuestion()];

        const sourceOperatorIds = Array.isArray(survey?.assignment?.operator_ids) ? survey.assignment.operator_ids : [];
        const activeOperatorIds = sanitizeOperatorIds(sourceOperatorIds, { excludeDismissed: true });
        const removedDismissedCount = Math.max(0, sanitizeOperatorIds(sourceOperatorIds).length - activeOperatorIds.length);

        setDraft({
            title: String(survey?.title || ''),
            description: String(survey?.description || ''),
            isTest: !!survey?.is_test,
            directionIds: (survey?.assignment?.direction_ids || []).map((id) => String(id)).filter(Boolean),
            groupIds: (survey?.assignment?.group_ids || []).map((id) => Number(id)).filter(Number.isFinite),
            tenureWeeksMin: survey?.assignment?.tenure_weeks_min != null ? String(survey.assignment.tenure_weeks_min) : '',
            tenureWeeksMax: survey?.assignment?.tenure_weeks_max != null ? String(survey.assignment.tenure_weeks_max) : '',
            operatorIds: activeOperatorIds,
            questions: clonedQuestions,
            // Повтор — это новый запуск: окно теста задаётся заново, чтобы не
            // создать копию с уже истёкшим временем.
            startsAt: '',
            endsAt: '',
            singleAttempt: survey?.test?.single_attempt !== false,
            affectsQuality: !!survey?.test?.affects_quality
        });
        setOperatorQuery('');
        setEditingSurveyId(null);
        setRepeatSourceSurveyId(sourceId);
        setShowBuilder(true);
        if (removedDismissedCount > 0) {
            notify(`Из повтора исключены уволенные операторы: ${removedDismissedCount}`, 'success');
        }
    }, [canManage, notify, sanitizeOperatorIds]);

    const startEditSurvey = useCallback((survey) => {
        if (!survey || !canManage) return;
        const surveyId = Number(survey?.id);
        if (!Number.isFinite(surveyId)) return;

        const sourceQuestions = Array.isArray(survey?.questions) ? survey.questions : [];
        const clonedQuestions = sourceQuestions.length > 0
            ? sourceQuestions.map((question) => {
                const rawType = String(question?.type || 'single');
                const isOtherOnlyQuestion = (
                    rawType === 'single'
                    && survey?.is_test !== true
                    && question?.allow_other === true
                    && (!Array.isArray(question?.options) || question.options.length === 0)
                );
                const type = isOtherOnlyQuestion
                    ? QUESTION_TYPE_OTHER_ONLY
                    : (survey?.is_test && rawType === 'rating' ? 'single' : rawType);
                return {
                    id: Number.isFinite(Number(question?.id)) ? Number(question.id) : `q_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
                    text: String(question?.text || ''),
                    type,
                    required: question?.required !== false,
                    allowOther: type === QUESTION_TYPE_OTHER_ONLY ? true : (survey?.is_test ? false : (question?.allow_other === true)),
                    options: (type === 'rating' || type === QUESTION_TYPE_OTHER_ONLY)
                        ? []
                        : (Array.isArray(question?.options) ? question.options.map((option) => String(option || '')) : ['', '']),
                    correctOptions: type === QUESTION_TYPE_OTHER_ONLY ? [] : toUniqueTrimmedList(question?.correct_options),
                    points: question?.points != null ? String(question.points) : '1',
                    partialCredit: question?.partial_credit === true && type === 'multiple'
                };
            })
            : [emptyQuestion()];

        setDraft({
            title: String(survey?.title || ''),
            description: String(survey?.description || ''),
            isTest: !!survey?.is_test,
            directionIds: (survey?.assignment?.direction_ids || []).map((id) => String(id)).filter(Boolean),
            groupIds: (survey?.assignment?.group_ids || []).map((id) => Number(id)).filter(Number.isFinite),
            tenureWeeksMin: survey?.assignment?.tenure_weeks_min != null ? String(survey.assignment.tenure_weeks_min) : '',
            tenureWeeksMax: survey?.assignment?.tenure_weeks_max != null ? String(survey.assignment.tenure_weeks_max) : '',
            operatorIds: sanitizeOperatorIds(survey?.assignment?.operator_ids || []),
            questions: clonedQuestions,
            startsAt: isoToLocalInput(survey?.test?.starts_at),
            endsAt: isoToLocalInput(survey?.test?.ends_at),
            singleAttempt: survey?.test?.single_attempt !== false,
            affectsQuality: !!survey?.test?.affects_quality
        });
        setOperatorQuery('');
        setRepeatSourceSurveyId(null);
        setEditingSurveyId(surveyId);
        setShowBuilder(true);
    }, [canManage, sanitizeOperatorIds]);

    const createSurvey = async () => {
        if (!String(draft.title || '').trim()) return notify('Укажите название опроса', 'error');
        const assignmentOperatorIds = sanitizeOperatorIds(draft.operatorIds, { excludeDismissed: true });
        if (!assignmentOperatorIds.length) return notify('Выберите минимум одного действующего оператора', 'error');
        const minWeeks = parseWeeksInput(draft.tenureWeeksMin);
        const maxWeeks = parseWeeksInput(draft.tenureWeeksMax);
        if (minWeeks != null && maxWeeks != null && minWeeks > maxWeeks) return notify('Минимальный стаж не может быть больше максимального', 'error');

        const normalizedQuestions = (draft.questions || []).map((question) => {
            const isOtherOnlyQuestion = question.type === QUESTION_TYPE_OTHER_ONLY;
            const payloadType = isOtherOnlyQuestion ? 'single' : question.type;
            const normalizedOptions = (payloadType === 'rating' || isOtherOnlyQuestion)
                ? []
                : toUniqueTrimmedList(question.options);
            const normalizedCorrectOptions = isOtherOnlyQuestion
                ? []
                : toUniqueTrimmedList(question.correctOptions).filter((option) => normalizedOptions.includes(option));
            const normalizedQuestion = {
                text: String(question.text || '').trim(),
                type: payloadType,
                required: !!question.required,
                allow_other: draft.isTest ? false : (payloadType === 'rating' ? false : (isOtherOnlyQuestion ? true : !!question.allowOther)),
                options: normalizedOptions,
                correct_options: normalizedCorrectOptions
            };
            if (draft.isTest) {
                normalizedQuestion.points = parsePointsInput(question.points) ?? 1;
                // Частичный зачёт возможен только там, где вариантов несколько.
                normalizedQuestion.partial_credit = payloadType === 'multiple' && !!question.partialCredit;
            }
            const numericQuestionId = Number(question.id);
            if (isEditMode && Number.isFinite(numericQuestionId) && numericQuestionId > 0) {
                normalizedQuestion.id = numericQuestionId;
            }
            return normalizedQuestion;
        });

        for (let i = 0; i < draft.questions.length; i += 1) {
            const sourceQuestion = draft.questions[i] || {};
            const question = normalizedQuestions[i];
            const isOtherOnlyQuestion = sourceQuestion.type === QUESTION_TYPE_OTHER_ONLY;
            if (!String(question.text || '').trim()) return notify(`Заполните текст вопроса #${i + 1}`, 'error');
            if (question.type !== 'rating' && !isOtherOnlyQuestion && (question.options || []).length < 2) {
                return notify(`Нужно минимум 2 варианта в вопросе #${i + 1}`, 'error');
            }
            if (draft.isTest && isOtherOnlyQuestion) {
                return notify(`В тесте нельзя использовать тип "Только Другое" (вопрос #${i + 1})`, 'error');
            }
            if (draft.isTest && question.type === 'rating') {
                return notify(`В тесте нельзя использовать рейтинг (вопрос #${i + 1})`, 'error');
            }
            if (draft.isTest && question.type !== 'rating') {
                if (!question.correct_options.length) {
                    return notify(`Укажите правильный ответ для вопроса #${i + 1}`, 'error');
                }
                const invalidCorrect = question.correct_options.filter((option) => !question.options.includes(option));
                if (invalidCorrect.length > 0) {
                    return notify(`Правильные ответы должны совпадать с вариантами в вопросе #${i + 1}`, 'error');
                }
                if (question.type === 'single' && question.correct_options.length !== 1) {
                    return notify(`Для одиночного выбора в вопросе #${i + 1} нужен ровно один правильный ответ`, 'error');
                }
                if (parsePointsInput(sourceQuestion.points) == null) {
                    return notify(`Баллы за вопрос #${i + 1} — число больше 0`, 'error');
                }
            }
        }

        const startsAtIso = draft.isTest ? localInputToIso(draft.startsAt) : null;
        const endsAtIso = draft.isTest ? localInputToIso(draft.endsAt) : null;
        if (draft.isTest && startsAtIso && endsAtIso && startsAtIso >= endsAtIso) {
            return notify('Завершение теста должно быть позже начала', 'error');
        }

        const payload = {
            title: String(draft.title || '').trim(),
            description: String(draft.description || '').trim(),
            is_test: !!draft.isTest,
            assignment: {
                direction_ids: (draft.directionIds || []).map((id) => Number(id)).filter(Number.isFinite),
                group_ids: (draft.groupIds || []).map((id) => Number(id)).filter(Number.isFinite),
                tenure_weeks_min: minWeeks,
                tenure_weeks_max: maxWeeks,
                operator_ids: assignmentOperatorIds
            },
            questions: normalizedQuestions,
            test: {
                starts_at: startsAtIso,
                ends_at: endsAtIso,
                single_attempt: draft.isTest ? !!draft.singleAttempt : true,
                affects_quality: draft.isTest ? !!draft.affectsQuality : false
            }
        };
        if (isRepeatMode) {
            payload.repeat_from_survey_id = Number(repeatSourceSurveyId);
        }

        setIsSaving(true);
        try {
            if (isEditMode) {
                await axios.put(`${apiBaseUrl}/api/surveys/${editingSurveyId}`, payload, { headers });
                notify('Опрос обновлен', 'success');
            } else {
                await axios.post(`${apiBaseUrl}/api/surveys`, payload, { headers });
                notify(isRepeatMode ? 'Повтор опроса создан' : 'Опрос создан', 'success');
            }
            closeBuilder();
            await loadSurveys();
        } catch (error) {
            notify(error?.response?.data?.error || (isEditMode ? 'Не удалось обновить опрос' : 'Не удалось создать опрос'), 'error');
        } finally {
            setIsSaving(false);
        }
    };

    const removeSurvey = async (surveyId) => {
        if (!window.confirm('Удалить опрос?')) return;
        try {
            await axios.delete(`${apiBaseUrl}/api/surveys/${surveyId}`, { headers });
            notify('Опрос удален', 'success');
            await loadSurveys();
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось удалить опрос', 'error');
        }
    };

    const submitSurvey = async () => {
        if (!selectedSurvey || !canFillSelectedSurvey) return;
        const preparedAnswers = (selectedSurvey.questions || []).map((question) => {
            const answer = answers[question.id] || {};
            const payload = { question_id: Number(question.id) };
            if (question.type === 'rating') {
                payload.rating_value = answer.rating_value === '' ? null : Number(answer.rating_value);
            } else {
                const selectedOptionsRaw = Array.isArray(answer.selected_options) ? answer.selected_options : [];
                const selectedOptions = selectedOptionsRaw.map((item) => String(item || '').trim()).filter(Boolean);
                const otherAnswerText = String(answer.answer_text || '').trim().slice(0, OTHER_ANSWER_MAX_LENGTH);

                if (question.type === 'single' && otherAnswerText) {
                    payload.selected_options = [];
                    payload.answer_text = otherAnswerText;
                } else {
                    payload.selected_options = question.type === 'single'
                        ? (selectedOptions[0] ? [selectedOptions[0]] : [])
                        : selectedOptions;
                    if (otherAnswerText) payload.answer_text = otherAnswerText;
                }
            }
            return payload;
        });

        setIsSubmitting(true);
        try {
            if (attemptSaveTimerRef.current) window.clearTimeout(attemptSaveTimerRef.current);
            const response = await axios.post(
                `${apiBaseUrl}/api/surveys/${selectedSurvey.id}/submit`,
                { answers: preparedAnswers },
                { headers }
            );
            const testSummary = response?.data?.result?.test_summary;
            if (selectedSurvey?.is_test && testSummary) {
                const percent = Number(testSummary.score_percent || 0);
                notify(`Тест пройден · результат ${percent.toFixed(1).replace(/\.0$/, '')}%`, 'success');
            } else {
                notify('Опрос успешно пройден', 'success');
            }
            setRetakeSurveyId(null);
            await loadSurveys();
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось отправить ответы', 'error');
            // Время могло истечь ровно во время отправки — тогда результат
            // закроет автоотправка, а список надо обновить.
            if (error?.response?.status === 409) await loadSurveys();
        } finally {
            setIsSubmitting(false);
        }
    };

    const exportSurveyStatsExcel = async () => {
        if (!selectedSurvey?.id || !apiBaseUrl) return;
        setIsStatsExporting(true);
        try {
            const response = await axios.get(
                `${apiBaseUrl}/api/surveys/${selectedSurvey.id}/export_excel`,
                {
                    headers,
                    responseType: 'blob'
                }
            );

            const contentDisposition = response?.headers?.['content-disposition'] || '';
            let filename = `survey_${selectedSurvey.id}_stats.xlsx`;
            const utf8NameMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
            const plainNameMatch = contentDisposition.match(/filename=\"?([^\";]+)\"?/i);
            if (utf8NameMatch?.[1]) {
                try {
                    filename = decodeURIComponent(utf8NameMatch[1]);
                } catch (e) {
                    filename = utf8NameMatch[1];
                }
            } else if (plainNameMatch?.[1]) {
                filename = plainNameMatch[1];
            }

            const blob = new Blob(
                [response.data],
                { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }
            );
            const objectUrl = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = objectUrl;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            window.URL.revokeObjectURL(objectUrl);

            notify('Статистика выгружена в Excel', 'success');
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось выгрузить статистику в Excel', 'error');
        } finally {
            setIsStatsExporting(false);
        }
    };

    const formatPercent = (value) => {
        const number = Number(value);
        if (!Number.isFinite(number)) return '0%';
        return `${number.toFixed(1).replace(/\.0$/, '')}%`;
    };

    const percentToWidth = (value) => {
        const number = Number(value);
        if (!Number.isFinite(number)) return '0%';
        return `${Math.max(0, Math.min(100, number))}%`;
    };

    const renderDetailedQuestionStats = (stat, index) => {
        if (!stat) return null;

        const statQuestionId = Number(stat?.question_id);
        const questionMeta = Number.isFinite(statQuestionId)
            ? selectedSurveyQuestionMetaById.get(statQuestionId)
            : null;
        const questionText = String(stat?.text || questionMeta?.text || `Вопрос ${index + 1}`);
        const answeredCount = Number(stat.responses_with_answer || 0);
        const respondentsTotal = Number(
            stat.survey_respondents_total != null
                ? stat.survey_respondents_total
                : (stat.respondents_total != null
                    ? stat.respondents_total
                    : selectedSurvey?.statistics?.responses_count || 0)
        );
        const skippedCount = Number(
            stat.skipped_count != null
                ? stat.skipped_count
                : Math.max(0, respondentsTotal - answeredCount)
        );
        const expectedOptions = toUniqueTrimmedList(
            (Array.isArray(stat?.correct_options) && stat.correct_options.length > 0)
                ? stat.correct_options
                : (questionMeta?.correctOptions || [])
        );
        const expectedOptionsSet = new Set(expectedOptions);

        const ratingDistribution = Array.isArray(stat.ratings_distribution_detailed) && stat.ratings_distribution_detailed.length
            ? stat.ratings_distribution_detailed
            : [1, 2, 3, 4, 5].map((value) => {
                const count = Number(stat?.ratings_distribution?.[String(value)] || 0);
                return {
                    value,
                    count,
                    percent_of_answers: answeredCount > 0 ? (count / answeredCount) * 100 : 0,
                    percent_of_respondents: respondentsTotal > 0 ? (count / respondentsTotal) * 100 : 0
                };
            });

        const options = Array.isArray(stat.options) ? stat.options : [];
        const topOptions = Array.isArray(stat.top_options) ? stat.top_options : [];

        return (
            <div key={`${selectedSurvey?.id || 'survey'}_stat_${index}`} className="border border-gray-100 rounded-xl p-4 space-y-3 bg-gray-50">
                {/* Question header */}
                <div className="flex items-start justify-between gap-2">
                    <div>
                        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-0.5">Вопрос #{index + 1}</div>
                        <div className="text-sm font-medium text-gray-800">{questionText}</div>
                    </div>
                    <Badge color={stat.type === 'rating' ? 'amber' : 'blue'}>
                        {questionTypeLabel(stat.type)}
                    </Badge>
                </div>

                {skippedCount > 0 && <div className="text-[11px] text-gray-400">Пропустили: {skippedCount}</div>}

                {/* Rating stats */}
                {stat.type === 'rating' && (
                    <div className="space-y-2">
                        <div className="flex gap-4 text-xs text-gray-600">
                            <span>Среднее: <strong className="text-gray-800">{stat.average_rating ?? '—'}</strong></span>
                            <span>Медиана: <strong className="text-gray-800">{stat.median_rating ?? '—'}</strong></span>
                            <span>Диапазон: <strong className="text-gray-800">{stat.min_rating ?? '—'}–{stat.max_rating ?? '—'}</strong></span>
                        </div>
                        <div className="space-y-1.5">
                            {ratingDistribution.map((bucket) => {
                                const value = Number(bucket.value);
                                const count = Number(bucket.count || 0);
                                const percentAnswers = Number(bucket.percent_of_answers || 0);
                                return (
                                    <div key={`${selectedSurvey?.id || 'survey'}_stat_${index}_rating_${value}`} className="flex items-center gap-2">
                                        <span className="text-[11px] w-8 shrink-0 text-gray-600">{value} ★</span>
                                        <div className="flex-1 h-2 bg-amber-100 rounded-full overflow-hidden">
                                            <div className="h-full bg-amber-400 rounded-full transition-all duration-500" style={{ width: percentToWidth(percentAnswers) }} />
                                        </div>
                                        <span className="text-[11px] text-gray-500 w-20 text-right shrink-0">{count} ({formatPercent(percentAnswers)})</span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* Choice stats */}
                {stat.type !== 'rating' && (
                    <div className="space-y-1.5">
                        {stat.type === 'multiple' && (
                            <div className="text-[11px] text-gray-500">
                                Всего выборов: <strong className="text-gray-700">{Number(stat.selections_total || 0)}</strong> (можно несколько)
                            </div>
                        )}
                        {options.length === 0 && <div className="text-[11px] text-gray-400">Данных пока нет.</div>}
                        {options.map((option, optionIndex) => {
                            const optionLabel = String(option?.option || `Вариант ${optionIndex + 1}`);
                            const optionCount = Number(option?.count || 0);
                            const percentRespondents = Number(option?.percent_of_respondents != null ? option.percent_of_respondents : option?.percent || 0);
                            const percentAnswers = Number(option?.percent_of_answers != null ? option.percent_of_answers : option?.percent || 0);
                            const isCorrectOption = isTestStatsSurvey && expectedOptionsSet.has(optionLabel);
                            return (
                                <div
                                    key={`${selectedSurvey?.id || 'survey'}_stat_${index}_option_${optionIndex}`}
                                    className={`space-y-1 ${isCorrectOption ? 'rounded-md border border-emerald-200 bg-emerald-50/70 p-1.5' : ''}`}
                                >
                                    <div className="flex items-center justify-between gap-2 text-[11px]">
                                        <span className={`truncate ${isCorrectOption ? 'text-emerald-700 font-semibold' : 'text-gray-700'}`} title={optionLabel}>
                                            {optionLabel}
                                        </span>
                                        <span className="shrink-0 text-gray-500">{optionCount} ({formatPercent(percentRespondents)})</span>
                                    </div>
                                    {isCorrectOption && (
                                        <div className="text-[10px] text-emerald-700 font-medium">
                                            Правильный ответ
                                        </div>
                                    )}
                                    <ProgressBar value={percentRespondents} color={isCorrectOption ? 'emerald' : 'blue'} />
                                </div>
                            );
                        })}
                        {topOptions.length > 0 && (
                            <div className="text-[11px] text-gray-500 pt-1 border-t border-gray-200">
                                Топ: {topOptions.map((o) => `${o.option} (${o.count})`).join(', ')}
                            </div>
                        )}
                    </div>
                )}
            </div>
        );
    };

    /* ─── render ─── */
    return (
        <div className="space-y-5">

            {/* ── Page header ── */}
            <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
                <div className="px-6 py-5 flex items-center justify-between gap-4 flex-wrap">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-xl bg-blue-600 flex items-center justify-center shadow-sm">
                            <FaIcon className="fas fa-list-alt text-white text-base" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-gray-900 leading-tight">Опросы</h2>
                            <p className="text-xs text-gray-500 mt-0.5">
                                {canManage ? 'Создание и назначение опросов по стажу, направлению и операторам' : 'Назначенные вам опросы'}
                            </p>
                        </div>
                    </div>
                    {canManage && (
                        <div className="flex items-center justify-end gap-2 flex-wrap">
                        {canFilterByDepartment && (
                            <div className="flex items-center gap-2">
                                <FaIcon className="fa-solid fa-layer-group text-gray-400" />
                                <select
                                    value={departmentFilter}
                                    onChange={(event) => setDepartmentFilter(event.target.value)}
                                    className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all"
                                    title="Фильтр по отделу"
                                >
                                    <option value="">Все отделы</option>
                                    {departmentOptions.map((department) => (
                                        <option key={department.id} value={department.id}>{department.name}</option>
                                    ))}
                                </select>
                            </div>
                        )}
                        <button
                            onClick={() => {
                                if (showBuilder) {
                                    closeBuilder();
                                    return;
                                }
                                if (!isRepeatMode) resetBuilder();
                                setShowBuilder(true);
                            }}
                            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all shadow-sm ${
                                showBuilder
                                    ? 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                                    : 'bg-blue-600 text-white hover:bg-blue-700'
                            }`}
                        >
                            <FaIcon className={`fas ${showBuilder ? 'fa-times' : 'fa-plus'} text-xs`} />
                            {showBuilder ? 'Отменить' : 'Создать опрос'}
                        </button>
                        </div>
                    )}
                </div>
            </div>

            {/* ── Survey Builder (iOS / macOS modal sheet) ── */}
            {canManage && showBuilder && (
                <div
                    className="fixed inset-0 z-[80] flex items-stretch justify-center bg-slate-900/40 backdrop-blur-md sm:items-center sm:p-6"
                    role="dialog"
                    aria-modal="true"
                    onClick={() => { if (!isSaving) closeBuilder(); }}
                    style={{ fontFamily: APPLE_FONT }}
                >
                    <div
                        className="flex w-full max-w-4xl flex-col overflow-hidden bg-slate-50 shadow-2xl ring-1 ring-slate-900/10 sm:max-h-[92vh] sm:rounded-3xl"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Header */}
                        <div className="relative flex items-center justify-between gap-3 border-b border-slate-200/70 bg-white/80 px-5 py-3.5 backdrop-blur-xl">
                            <div className="flex min-w-0 items-center gap-3">
                                <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl shadow-sm ${
                                    isEditMode ? 'bg-slate-900' : (isRepeatMode ? 'bg-indigo-500' : 'bg-blue-600')
                                }`}>
                                    <FaIcon className={`fas ${isEditMode ? 'fa-pen' : (isRepeatMode ? 'fa-redo' : 'fa-plus')} text-xs text-white`} />
                                </div>
                                <div className="min-w-0">
                                    <div className="text-[15px] font-semibold leading-tight text-slate-900">
                                        {isEditMode ? 'Редактирование' : (isRepeatMode ? 'Повтор опроса' : (draft.isTest ? 'Новый тест' : 'Новый опрос'))}
                                    </div>
                                    <div className="mt-0.5 truncate text-[12px] text-slate-500">
                                        {draft.title?.trim() ? draft.title : 'Название, вопросы и операторы'}
                                    </div>
                                </div>
                            </div>
                            <button
                                type="button"
                                onClick={() => { if (!isSaving) closeBuilder(); }}
                                disabled={isSaving}
                                className="shrink-0 rounded-full p-2 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50"
                                aria-label="Закрыть"
                            >
                                <FaIcon className="fas fa-times text-sm" />
                            </button>
                        </div>

                        {/* Body */}
                        <div className="flex-1 space-y-5 overflow-y-auto px-4 py-5 sm:px-5">

                            {/* Repeat-mode hint */}
                            {isRepeatMode && (
                                <div className="flex items-start gap-3 rounded-2xl border border-indigo-100 bg-indigo-50/70 px-4 py-3">
                                    <FaIcon className="fas fa-redo mt-0.5 text-indigo-500 text-xs" />
                                    <div className="text-[12.5px] leading-relaxed text-indigo-900">
                                        Это повтор опроса. Операторы из прошлого запуска уже выбраны ниже — проверьте список, снимите лишних или добавьте других действующих сотрудников. Уволенные сотрудники исключены автоматически.
                                    </div>
                                </div>
                            )}

                            {/* Основное */}
                            <IosSection title="Основное">
                                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                                    <div>
                                        <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Название опроса *</label>
                                        <input
                                            value={draft.title}
                                            onChange={(e) => setDraft((p) => ({ ...p, title: e.target.value }))}
                                            placeholder="Например: Опрос удовлетворённости"
                                            className={iosInput}
                                        />
                                    </div>
                                    <div>
                                        <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Описание</label>
                                        <input
                                            value={draft.description}
                                            onChange={(e) => setDraft((p) => ({ ...p, description: e.target.value }))}
                                            placeholder="Краткое описание (необязательно)"
                                            className={iosInput}
                                        />
                                    </div>
                                </div>
                                <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3.5 py-2.5 ring-1 ring-slate-200/60">
                                    <div className="min-w-0">
                                        <div className="text-[14px] font-medium text-slate-800">Режим теста</div>
                                        <div className="text-[12px] text-slate-500">Правильные ответы и автоматическая проверка</div>
                                    </div>
                                    <IosToggle checked={!!draft.isTest} onChange={(value) => toggleTestMode(value)} />
                                </div>
                                {draft.isTest && (
                                    <div className="px-1 text-[11.5px] text-amber-600">
                                        В тесте недоступны вопросы типа «рейтинг» и вариант «Другое».
                                    </div>
                                )}
                            </IosSection>

                            {/* Расписание и правила теста */}
                            {draft.isTest && (
                                <IosSection
                                    title="Расписание теста"
                                    hint="До времени начала тест не виден оператору. Начатая попытка отправится автоматически, когда время закончится — с уже выбранными ответами."
                                >
                                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                                        <div>
                                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Начало</label>
                                            <input
                                                type="datetime-local"
                                                value={draft.startsAt}
                                                onChange={(e) => setDraft((p) => ({ ...p, startsAt: e.target.value }))}
                                                className={`${iosInput} tabular-nums`}
                                            />
                                        </div>
                                        <div>
                                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Завершение</label>
                                            <input
                                                type="datetime-local"
                                                value={draft.endsAt}
                                                min={draft.startsAt || undefined}
                                                onChange={(e) => setDraft((p) => ({ ...p, endsAt: e.target.value }))}
                                                className={`${iosInput} tabular-nums`}
                                            />
                                        </div>
                                    </div>
                                    <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3.5 py-2.5 ring-1 ring-slate-200/60">
                                        <div className="min-w-0">
                                            <div className="text-[14px] font-medium text-slate-800">Одна попытка</div>
                                            <div className="text-[12px] text-slate-500">Пройти тест можно только один раз</div>
                                        </div>
                                        <IosToggle
                                            checked={!!draft.singleAttempt}
                                            onChange={(value) => setDraft((p) => ({ ...p, singleAttempt: !!value }))}
                                        />
                                    </div>
                                    <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3.5 py-2.5 ring-1 ring-slate-200/60">
                                        <div className="min-w-0">
                                            <div className="text-[14px] font-medium text-slate-800">Учитывать результат в качестве оператора</div>
                                            <div className="text-[12px] text-slate-500">
                                                Результат попадёт в журнал оценок как «Тестирование знаний»: войдёт в средний балл, но план по звонкам не уменьшит
                                            </div>
                                        </div>
                                        <IosToggle
                                            checked={!!draft.affectsQuality}
                                            onChange={(value) => setDraft((p) => ({ ...p, affectsQuality: !!value }))}
                                        />
                                    </div>
                                </IosSection>
                            )}

                            {/* Фильтры назначения */}
                            <IosSection
                                title="Фильтры назначения"
                                hint="Фильтры помогают быстро отобрать операторов. Уже выбранные остаются в списке даже вне фильтров."
                            >
                                <div className="grid grid-cols-2 gap-3">
                                    <div>
                                        <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Стаж от (недель)</label>
                                        <input
                                            type="number" min="0"
                                            value={draft.tenureWeeksMin}
                                            onChange={(e) => setDraft((p) => ({ ...p, tenureWeeksMin: e.target.value }))}
                                            placeholder="Минимум"
                                            className={iosInput}
                                        />
                                    </div>
                                    <div>
                                        <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Стаж до (недель)</label>
                                        <input
                                            type="number" min="0"
                                            value={draft.tenureWeeksMax}
                                            onChange={(e) => setDraft((p) => ({ ...p, tenureWeeksMax: e.target.value }))}
                                            placeholder="Максимум"
                                            className={iosInput}
                                        />
                                    </div>
                                </div>

                                {canFilterByDepartment && (
                                    <div>
                                        <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Отдел</label>
                                        <select
                                            value={departmentFilter}
                                            onChange={(event) => setDepartmentFilter(event.target.value)}
                                            className={iosInput}
                                        >
                                            <option value="">Все отделы</option>
                                            {departmentOptions.map((department) => (
                                                <option key={department.id} value={department.id}>{department.name}</option>
                                            ))}
                                        </select>
                                    </div>
                                )}

                                {directionNameById.size > 0 && (
                                    <div>
                                        <label className="mb-1.5 block px-1 text-[12px] font-medium text-slate-500">Направления</label>
                                        <div className="flex flex-wrap gap-1.5">
                                            {Array.from(directionNameById.entries()).map(([id, name]) => {
                                                const active = draft.directionIds.includes(id);
                                                return (
                                                    <button
                                                        key={id}
                                                        onClick={() => toggleArrayValue(setDraft, 'directionIds', id)}
                                                        className={`rounded-full px-3 py-1.5 text-[12.5px] font-medium transition-all ${
                                                            active
                                                                ? 'bg-blue-600 text-white shadow-sm'
                                                                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                                        }`}
                                                    >
                                                        {name}
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    </div>
                                )}

                                {groupOptions.length > 0 && (
                                    <div>
                                        <label className="mb-1.5 block px-1 text-[12px] font-medium text-slate-500">Группы</label>
                                        <div className="flex flex-wrap gap-1.5">
                                            {groupOptions.map((group) => {
                                                const groupId = Number(group?.id);
                                                const active = (draft.groupIds || []).some((id) => Number(id) === groupId);
                                                const membersCount = (group?.operator_ids || []).length;
                                                return (
                                                    <button
                                                        key={groupId}
                                                        type="button"
                                                        onClick={() => toggleArrayValue(setDraft, 'groupIds', groupId)}
                                                        className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium transition-all ${
                                                            active
                                                                ? 'bg-blue-600 text-white shadow-sm'
                                                                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                                        }`}
                                                    >
                                                        {group?.name || `Группа #${groupId}`}
                                                        <span className={`tabular-nums text-[11px] ${active ? 'text-blue-100' : 'text-slate-400'}`}>
                                                            {membersCount}
                                                        </span>
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    </div>
                                )}
                            </IosSection>

                            {/* Операторы */}
                            <IosSection
                                title="Операторы *"
                                right={draft.operatorIds.length > 0 && (
                                    <span className="rounded-full bg-blue-100 px-2.5 py-0.5 text-[11px] font-semibold text-blue-700">
                                        {draft.operatorIds.length} выбрано
                                    </span>
                                )}
                            >
                                <div className="relative">
                                    <FaIcon className="fas fa-search absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-300 text-xs" />
                                    <input
                                        value={operatorQuery}
                                        onChange={(e) => setOperatorQuery(e.target.value)}
                                        placeholder="Поиск по имени или направлению"
                                        className={`${iosInput} pl-9`}
                                    />
                                </div>
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div className="px-1 text-[11.5px] text-slate-500">
                                        По фильтрам: <strong className="text-slate-700">{selectedFilteredOperatorsCount}/{filteredAssignableOperatorIds.length}</strong>
                                        {filteredOperators.some((operator) => operator.isDismissed) && (
                                            <span className="ml-1 text-amber-600">· уволенные показаны только для контроля</span>
                                        )}
                                    </div>
                                    <div className="flex flex-wrap items-center gap-1.5">
                                        <button
                                            type="button"
                                            onClick={selectAllFilteredOperators}
                                            disabled={!hasFilteredOperators || allFilteredOperatorsSelected}
                                            className="rounded-lg bg-blue-50 px-2.5 py-1 text-[12px] font-medium text-blue-600 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-40"
                                        >
                                            Выбрать всех
                                        </button>
                                        <button
                                            type="button"
                                            onClick={clearFilteredOperators}
                                            disabled={!hasFilteredOperators || selectedFilteredOperatorsCount === 0}
                                            className="rounded-lg bg-slate-100 px-2.5 py-1 text-[12px] font-medium text-slate-600 hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-40"
                                        >
                                            Снять
                                        </button>
                                    </div>
                                </div>
                                <div className="max-h-56 overflow-y-auto rounded-xl bg-slate-50 ring-1 ring-slate-200/60">
                                    {filteredOperators.length === 0 && (
                                        <div className="px-3 py-6 text-center text-[12.5px] text-slate-400">
                                            <FaIcon className="fas fa-user-slash mb-1.5 block text-base text-slate-300" />
                                            Операторы не найдены
                                        </div>
                                    )}
                                    <div className="divide-y divide-slate-200/60">
                                        {filteredOperators.map((operator) => {
                                            const checked = draft.operatorIds.includes(operator.id);
                                            const canToggleOperator = !operator.isDismissed || checked;
                                            const initial = String(operator.name || '?').trim().charAt(0).toUpperCase() || '?';
                                            return (
                                                <label
                                                    key={operator.id}
                                                    className={`flex items-center gap-3 px-3 py-2.5 transition-colors ${
                                                        checked ? 'bg-blue-50' : 'hover:bg-white'
                                                    } ${canToggleOperator ? 'cursor-pointer' : 'cursor-not-allowed opacity-70'}`}
                                                >
                                                    <div className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-all ${
                                                        checked ? 'border-blue-600 bg-blue-600' : 'border-slate-300 bg-white'
                                                    }`}>
                                                        {checked && <FaIcon className="fas fa-check text-[9px] text-white" />}
                                                    </div>
                                                    <input
                                                        type="checkbox"
                                                        className="hidden"
                                                        checked={checked}
                                                        onChange={() => {
                                                            if (!canToggleOperator) {
                                                                notify('Уволенного оператора нельзя назначить в опрос', 'error');
                                                                return;
                                                            }
                                                            toggleArrayValue(setDraft, 'operatorIds', operator.id);
                                                        }}
                                                    />
                                                    <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
                                                        operator.isDismissed ? 'bg-amber-100 text-amber-700' : 'bg-slate-200 text-slate-600'
                                                    }`}>
                                                        {initial}
                                                    </div>
                                                    <div className="min-w-0 flex-1">
                                                        <div className="truncate text-[13.5px] font-medium text-slate-800">{operator.name}</div>
                                                        <div className="truncate text-[11px] text-slate-400">
                                                            {operator.directionName} · {operator.tenureLabel}
                                                        </div>
                                                    </div>
                                                    {operator.isDismissed && (
                                                        <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">Уволен</span>
                                                    )}
                                                </label>
                                            );
                                        })}
                                    </div>
                                </div>
                            </IosSection>

                            {/* Вопросы */}
                            <IosSection title={`Вопросы · ${draft.questions.length}`}>
                                <div className="space-y-3">
                                    {draft.questions.map((question, index) => {
                                        const options = Array.isArray(question.options) ? question.options : [];
                                        const availableQuestionTypes = draft.isTest
                                            ? QUESTION_TYPES.filter((item) => item.value !== 'rating' && item.value !== QUESTION_TYPE_OTHER_ONLY)
                                            : QUESTION_TYPES;
                                        return (
                                            <div key={question.id} className="rounded-2xl bg-slate-50 p-3.5 ring-1 ring-slate-200/60">
                                                <div className="mb-2.5 flex items-center justify-between gap-2">
                                                    <span className="flex h-6 items-center rounded-full bg-blue-100 px-2.5 text-[11px] font-bold text-blue-700">
                                                        Вопрос {index + 1}
                                                    </span>
                                                    <div className="flex items-center gap-2">
                                                        {draft.isTest && (
                                                            <label className="flex items-center gap-1.5 text-[12px] text-slate-500">
                                                                Баллы
                                                                <input
                                                                    type="number"
                                                                    min="0.5"
                                                                    step="0.5"
                                                                    value={question.points ?? '1'}
                                                                    onChange={(e) => updateQuestion(question.id, { points: e.target.value })}
                                                                    className="w-16 rounded-lg border-0 bg-white px-2 py-1 text-right text-[12.5px] tabular-nums text-slate-900 ring-1 ring-slate-200/60 focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                                                                />
                                                            </label>
                                                        )}
                                                        <button
                                                            disabled={draft.questions.length <= 1}
                                                            onClick={() => setDraft((p) => ({ ...p, questions: p.questions.filter((item) => item.id !== question.id) }))}
                                                            className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-red-50 hover:text-red-500 disabled:opacity-30"
                                                            title="Удалить вопрос"
                                                        >
                                                            <FaIcon className="fas fa-trash-alt text-xs" />
                                                        </button>
                                                    </div>
                                                </div>

                                                <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                                                    <div className="md:col-span-2">
                                                        <input
                                                            value={question.text}
                                                            onChange={(e) => updateQuestion(question.id, { text: e.target.value })}
                                                            placeholder="Текст вопроса"
                                                            className={`${iosInput} bg-white ring-1 ring-slate-200/60`}
                                                        />
                                                    </div>
                                                    <select
                                                        value={question.type}
                                                        onChange={(e) => {
                                                            const nextType = e.target.value;
                                                            const normalizedOptions = toUniqueTrimmedList(question.options);
                                                            let nextCorrectOptions = toUniqueTrimmedList(question.correctOptions)
                                                                .filter((option) => normalizedOptions.includes(option));
                                                            if ((nextType === 'single' || nextType === QUESTION_TYPE_OTHER_ONLY) && nextCorrectOptions.length > 1) {
                                                                nextCorrectOptions = [nextCorrectOptions[0]];
                                                            }
                                                            updateQuestion(question.id, {
                                                                type: nextType,
                                                                allowOther: nextType === QUESTION_TYPE_OTHER_ONLY
                                                                    ? true
                                                                    : (draft.isTest ? false : (nextType === 'rating' ? false : question.allowOther)),
                                                                options: (nextType === 'rating' || nextType === QUESTION_TYPE_OTHER_ONLY)
                                                                    ? []
                                                                    : (question.options?.length ? question.options : ['', '']),
                                                                correctOptions: nextType === QUESTION_TYPE_OTHER_ONLY ? [] : nextCorrectOptions,
                                                                // Один ответ — частичного зачёта быть не может.
                                                                partialCredit: nextType === 'multiple' ? !!question.partialCredit : false
                                                            });
                                                        }}
                                                        className={`${iosInput} bg-white ring-1 ring-slate-200/60`}
                                                    >
                                                        {availableQuestionTypes.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                                                    </select>
                                                </div>

                                                {question.type !== 'rating' && (
                                                    <div className="mt-3 space-y-2">
                                                        <div className="px-1 text-[11px] font-medium text-slate-400">Варианты ответа</div>
                                                        {options.map((option, optionIndex) => {
                                                            const normalizedOption = String(option || '').trim();
                                                            const isCorrectOption = draft.isTest
                                                                && normalizedOption
                                                                && toUniqueTrimmedList(question.correctOptions).includes(normalizedOption);

                                                            return (
                                                                <div key={`${question.id}_${optionIndex}`} className="flex items-center gap-2">
                                                                    {draft.isTest ? (
                                                                        <button
                                                                            type="button"
                                                                            onClick={() => toggleCorrectOption(question.id, option)}
                                                                            className={`flex h-5 w-5 shrink-0 items-center justify-center border-2 transition-all ${
                                                                                question.type === 'single' ? 'rounded-full' : 'rounded'
                                                                            } ${
                                                                                isCorrectOption
                                                                                    ? 'border-emerald-500 bg-emerald-500 text-white'
                                                                                    : 'border-slate-300 text-transparent hover:border-emerald-400'
                                                                            }`}
                                                                            title={isCorrectOption ? 'Правильный вариант' : 'Отметить как правильный'}
                                                                        >
                                                                            <FaIcon className="fas fa-check text-[9px]" />
                                                                        </button>
                                                                    ) : (
                                                                        <div className="h-5 w-5 shrink-0 rounded-full border-2 border-slate-200" />
                                                                    )}
                                                                    <input
                                                                        value={option}
                                                                        onChange={(e) => {
                                                                            const prevOptionTrimmed = String(options[optionIndex] || '').trim();
                                                                            const nextOptionValue = e.target.value;
                                                                            const nextOptionTrimmed = String(nextOptionValue || '').trim();
                                                                            const nextCorrectOptions = toUniqueTrimmedList(
                                                                                (question.correctOptions || []).map((value) => {
                                                                                    const normalizedValue = String(value || '').trim();
                                                                                    if (!normalizedValue) return '';
                                                                                    if (normalizedValue !== prevOptionTrimmed) return normalizedValue;
                                                                                    return nextOptionTrimmed;
                                                                                })
                                                                            );
                                                                            updateQuestion(question.id, {
                                                                                options: options.map((cur, idx) => (idx === optionIndex ? nextOptionValue : cur)),
                                                                                correctOptions: nextCorrectOptions
                                                                            });
                                                                        }}
                                                                        placeholder={`Вариант ${optionIndex + 1}`}
                                                                        className={`${iosInput} bg-white ring-1 ring-slate-200/60`}
                                                                    />
                                                                    <button
                                                                        type="button"
                                                                        disabled={options.length <= 2}
                                                                        onClick={() => removeQuestionOption(question.id, optionIndex)}
                                                                        className="px-1 text-slate-300 transition-colors hover:text-red-400 disabled:opacity-20"
                                                                    >
                                                                        <FaIcon className="fas fa-times" />
                                                                    </button>
                                                                </div>
                                                            );
                                                        })}
                                                        {question.type !== QUESTION_TYPE_OTHER_ONLY && (
                                                            <button
                                                                type="button"
                                                                onClick={() => addQuestionOption(question.id)}
                                                                className="ml-7 text-[12.5px] font-medium text-blue-600 transition-colors hover:text-blue-700"
                                                            >
                                                                <FaIcon className="fas fa-plus mr-1 text-[10px]" />Добавить вариант
                                                            </button>
                                                        )}

                                                        {draft.isTest ? (
                                                            <div className="ml-7 space-y-2">
                                                                <div className="text-[11.5px] text-emerald-600">
                                                                    Отметьте правильные варианты слева от текста ответа.
                                                                </div>
                                                                {/* Частичный зачёт нужен только там, где правильных
                                                                    вариантов может быть несколько. */}
                                                                {question.type === 'multiple' && (
                                                                    <div className="flex items-center justify-between gap-3 rounded-xl bg-white px-3 py-2.5 ring-1 ring-slate-200/60">
                                                                        <div className="min-w-0">
                                                                            <div className="text-[13px] font-medium text-slate-800">
                                                                                {question.partialCredit ? 'Частичный зачёт' : 'Всё или ничего'}
                                                                            </div>
                                                                            <div className="text-[11.5px] leading-snug text-slate-500">
                                                                                {question.partialCredit
                                                                                    ? 'Балл начисляется за угаданные варианты. Один лишний вариант — 0 за вопрос.'
                                                                                    : 'Балл только за все правильные варианты сразу.'}
                                                                            </div>
                                                                        </div>
                                                                        <IosToggle
                                                                            checked={!!question.partialCredit}
                                                                            onChange={(value) => updateQuestion(question.id, { partialCredit: !!value })}
                                                                        />
                                                                    </div>
                                                                )}
                                                            </div>
                                                        ) : question.type === QUESTION_TYPE_OTHER_ONLY ? (
                                                            <div className="ml-7 text-[11.5px] text-slate-500">
                                                                Для этого типа доступно только поле «Другое» без фиксированных вариантов.
                                                            </div>
                                                        ) : (
                                                            <label className="ml-7 inline-flex items-center gap-2 text-[12.5px] text-slate-500">
                                                                <input
                                                                    type="checkbox"
                                                                    checked={!!question.allowOther}
                                                                    onChange={(e) => updateQuestion(question.id, { allowOther: e.target.checked })}
                                                                    className="rounded border-slate-300"
                                                                />
                                                                Разрешить вариант «Другое»
                                                            </label>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>

                                <button
                                    onClick={() => setDraft((p) => ({ ...p, questions: [...p.questions, emptyQuestion()] }))}
                                    className="w-full rounded-xl border-2 border-dashed border-slate-200 py-2.5 text-[13px] font-medium text-slate-400 transition-all hover:border-blue-300 hover:text-blue-500"
                                >
                                    <FaIcon className="fas fa-plus mr-2 text-[11px]" />Добавить вопрос
                                </button>
                            </IosSection>
                        </div>

                        {/* Footer */}
                        <div className="flex items-center justify-between gap-3 border-t border-slate-200/70 bg-white/80 px-5 py-3 backdrop-blur-xl">
                            <div className="hidden text-[12px] text-slate-500 sm:block">
                                {draft.questions.length} вопр. · {draft.operatorIds.length} оператор(ов)
                            </div>
                            <div className="flex w-full items-center justify-end gap-2 sm:w-auto">
                                <button
                                    type="button"
                                    onClick={() => { if (!isSaving) closeBuilder(); }}
                                    disabled={isSaving}
                                    className="rounded-xl px-4 py-2.5 text-[13.5px] font-medium text-slate-600 transition-colors hover:bg-slate-100 disabled:opacity-50"
                                >
                                    Отмена
                                </button>
                                <button
                                    onClick={createSurvey}
                                    disabled={isSaving}
                                    className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-2.5 text-[13.5px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 disabled:opacity-50"
                                >
                                    {isSaving
                                        ? <><FaIcon className="fas fa-spinner fa-spin" />Сохранение…</>
                                        : <><FaIcon className="fas fa-check" />{isEditMode ? 'Обновить' : (draft.isTest ? 'Сохранить тест' : 'Сохранить опрос')}</>}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* ── Main content: list + detail ── */}
            <div className="grid grid-cols-1 xl:grid-cols-5 gap-5">

                {/* Survey list */}
                <div className="xl:col-span-2 bg-white rounded-2xl border border-gray-200 shadow-sm flex flex-col overflow-hidden">
                    <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                        <span className="text-sm font-semibold text-gray-800">Список опросов</span>
                        {visibleSurveys.length > 0 && <Badge color="gray">{visibleSurveys.length}</Badge>}
                    </div>

                    <div className="flex-1 overflow-y-auto divide-y divide-gray-50">
                        {isLoading && <SurveysListSkeleton />}
                        {!isLoading && visibleSurveys.length === 0 && (
                            <div className="p-8 text-center">
                                <div className="w-12 h-12 rounded-2xl bg-gray-50 flex items-center justify-center mx-auto mb-3">
                                    <FaIcon className="fas fa-clipboard-list text-gray-300 text-xl" />
                                </div>
                                <p className="text-sm text-gray-400">
                                    {isOperator ? 'Назначенных опросов пока нет' : 'Опросов пока нет'}
                                </p>
                            </div>
                        )}
                        {!isLoading && visibleSurveys.map((survey) => {
                            const isSelected = String(survey.id) === String(selectedSurveyId);
                            const isCompleted = survey?.my_assignment?.status === 'completed';
                            const displayMetrics = getSurveyDisplayMetrics(survey);
                            const completionRate = displayMetrics.completionRate || 0;
                            const repeatIteration = Number(survey?.repeat?.iteration || 1);
                            const listTestStatus = survey?.is_test ? testStatusMeta(survey?.test?.status) : null;
                            return (
                                <div
                                    key={survey.id}
                                    className={`group relative px-4 py-3 transition-colors cursor-pointer ${isSelected ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                                    onClick={() => setSelectedSurveyId(survey.id)}
                                >
                                    {isSelected && <div className="absolute left-0 inset-y-0 w-0.5 bg-blue-500 rounded-r-full" />}
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-1.5 min-w-0">
                                                <div className={`text-sm font-semibold truncate ${isSelected ? 'text-blue-700' : 'text-gray-800'}`}>
                                                    {survey.title}
                                                </div>
                                                {survey?.is_test && (
                                                    <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">
                                                        Тест
                                                    </span>
                                                )}
                                                {repeatIteration > 1 && (
                                                    <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">
                                                        #{repeatIteration}
                                                    </span>
                                                )}
                                            </div>
                                            <div className="mt-1.5 space-y-1.5">
                                                {listTestStatus && (
                                                    <Badge color={listTestStatus.color}>{listTestStatus.label}</Badge>
                                                )}
                                                {canManage ? (
                                                    <div className="space-y-1">
                                                        <div className="flex items-center justify-between text-[11px] text-gray-500">
                                                            <span>Пройдено: {displayMetrics.completedCount || 0} из {displayMetrics.assignedCount || 0}</span>
                                                            <span className="tabular-nums">{completionRate}%</span>
                                                        </div>
                                                        <ProgressBar value={completionRate} color={completionRate >= 80 ? 'emerald' : 'blue'} />
                                                    </div>
                                                ) : (
                                                    <Badge color={isCompleted ? 'green' : 'amber'}>
                                                        {isCompleted ? 'Пройден' : 'Назначен'}
                                                    </Badge>
                                                )}
                                            </div>
                                        </div>
                                        {canManage && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); removeSurvey(survey.id); }}
                                                className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg hover:bg-red-50 text-red-600 transition-colors text-xs font-medium"
                                                title="Удалить"
                                            >
                                                <FaIcon className="fas fa-trash-alt text-xs" />
                                                <span>Удалить</span>
                                            </button>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>

                {/* Survey detail */}
                <div className="xl:col-span-3 bg-white rounded-2xl border border-gray-200 shadow-sm flex flex-col overflow-hidden">
                    {!selectedSurvey ? (
                        <div className="flex-1 flex items-center justify-center p-12 text-center">
                            <div>
                                <div className="w-14 h-14 rounded-2xl bg-gray-50 flex items-center justify-center mx-auto mb-3">
                                    <FaIcon className="fas fa-hand-point-left text-gray-300 text-2xl" />
                                </div>
                                <p className="text-sm text-gray-400">Выберите опрос из списка</p>
                            </div>
                        </div>
                    ) : (
                        <>
                            {/* Detail header */}
                            <div className="px-5 py-4 border-b border-gray-100">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h3 className="text-base font-bold text-gray-900">{selectedSurvey.title}</h3>
                                            {selectedSurvey?.is_test && <Badge color="blue">Тест</Badge>}
                                            {selectedSurvey?.is_test && testStatusMeta(liveTestStatus) && (
                                                <Badge color={testStatusMeta(liveTestStatus).color}>
                                                    <FaIcon className={`fas ${testStatusMeta(liveTestStatus).icon} mr-1 text-[9px]`} />
                                                    {testStatusMeta(liveTestStatus).label}
                                                </Badge>
                                            )}
                                        </div>
                                        {selectedSurvey.description && (
                                            <p className="text-sm text-gray-500 mt-0.5">{selectedSurvey.description}</p>
                                        )}
                                        {Number(selectedSurvey?.repeat?.iteration || 1) > 1 && (
                                            <p className="text-xs text-blue-600 mt-1">
                                                Повторение #{Number(selectedSurvey?.repeat?.iteration || 1)}
                                            </p>
                                        )}
                                    </div>
                                    {canManage && (
                                        <div className="flex items-center gap-2">
                                            <button
                                                type="button"
                                                onClick={() => startEditSurvey(selectedSurvey)}
                                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 transition-colors"
                                                title="Редактировать опрос"
                                            >
                                                <FaIcon className="fas fa-edit" />
                                                Редактировать
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => startRepeatSurvey(selectedSurvey)}
                                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors"
                                                title="Создать повтор опроса"
                                            >
                                                <FaIcon className="fas fa-redo" />
                                                Повторить
                                            </button>
                                        </div>
                                    )}
                                    {isOperator && (
                                        <Badge color={selectedSurvey?.my_assignment?.status === 'completed' ? 'green' : 'amber'}>
                                            {selectedSurvey?.my_assignment?.status === 'completed' ? 'Пройден' : 'Назначен'}
                                        </Badge>
                                    )}
                                </div>

                                {/* Окно теста: одно место, где видно расписание и правила */}
                                {selectedSurvey?.is_test && (
                                    <div className="flex flex-wrap gap-2 mt-3">
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                            <FaIcon className="fas fa-hourglass-start text-gray-400 text-[10px]" />
                                            Начало: <strong className="text-gray-700 tabular-nums">{formatSurveyDateTime(selectedTestInfo?.starts_at)}</strong>
                                        </div>
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                            <FaIcon className="fas fa-hourglass-end text-gray-400 text-[10px]" />
                                            Завершение: <strong className="text-gray-700 tabular-nums">{formatSurveyDateTime(selectedTestInfo?.ends_at)}</strong>
                                        </div>
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                            <FaIcon className="fas fa-star text-gray-400 text-[10px]" />
                                            Максимум: <strong className="text-gray-700 tabular-nums">{formatPoints(selectedTestInfo?.max_points)}</strong> балл.
                                        </div>
                                        {selectedTestInfo?.single_attempt !== false && (
                                            <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                                <FaIcon className="fas fa-lock text-gray-400 text-[10px]" />
                                                Одна попытка
                                            </div>
                                        )}
                                        {selectedTestInfo?.affects_quality && (
                                            <div className="flex items-center gap-1.5 text-xs text-blue-700 bg-blue-50 rounded-lg px-2.5 py-1.5">
                                                <FaIcon className="fas fa-award text-blue-400 text-[10px]" />
                                                Идёт в качество оператора
                                            </div>
                                        )}
                                    </div>
                                )}

                                {/* Meta row */}
                                {(!canManage || activeTab === 'stats') && (
                                    <div className="flex flex-wrap gap-2 mt-3">
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                            <FaIcon className="fas fa-users text-gray-400 text-[10px]" />
                                            Операторов: <strong className="text-gray-700">{selectedSurveyDisplayMetrics.assignedCount || 0}</strong>
                                        </div>
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                            <FaIcon className="fas fa-clock text-gray-400 text-[10px]" />
                                            Стаж:{' '}
                                            <strong className="text-gray-700">
                                                {selectedSurvey?.assignment?.tenure_weeks_min != null || selectedSurvey?.assignment?.tenure_weeks_max != null
                                                    ? `${selectedSurvey?.assignment?.tenure_weeks_min != null ? `от ${selectedSurvey.assignment.tenure_weeks_min} нед.` : 'без минимума'}${selectedSurvey?.assignment?.tenure_weeks_max != null ? ` до ${selectedSurvey.assignment.tenure_weeks_max} нед.` : ''}`
                                                    : 'Любой'}
                                            </strong>
                                        </div>
                                        {canManage && (
                                            <>
                                                <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                                    <FaIcon className="fas fa-check-circle text-gray-400 text-[10px]" />
                                                    Пройдено: <strong className="text-gray-700">{selectedSurveyDisplayMetrics.completedCount || 0} / {selectedSurveyDisplayMetrics.assignedCount || 0}</strong>
                                                </div>
                                                <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                                    <FaIcon className="fas fa-hourglass-half text-gray-400 text-[10px]" />
                                                    Ожидают: <strong className="text-gray-700">{selectedSurveyDisplayMetrics.pendingCount || 0}</strong>
                                                </div>
                                            </>
                                        )}
                                    </div>
                                )}
                                {canManage && activeTab === 'questions' && (
                                    <div className="flex flex-wrap gap-2 mt-3">
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-blue-50 rounded-lg px-2.5 py-1.5">
                                            <FaIcon className="fas fa-list-ul text-blue-400 text-[10px]" />
                                            Вопросов: <strong className="text-gray-700">{(selectedSurvey?.questions || []).length}</strong>
                                        </div>
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-blue-50 rounded-lg px-2.5 py-1.5">
                                            <FaIcon className="fas fa-asterisk text-blue-400 text-[10px]" />
                                            Обязательных:{' '}
                                            <strong className="text-gray-700">
                                                {(selectedSurvey?.questions || []).filter((question) => question?.required).length}
                                            </strong>
                                        </div>
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-blue-50 rounded-lg px-2.5 py-1.5">
                                            <FaIcon className="fas fa-comment-dots text-blue-400 text-[10px]" />
                                            С полем «Другое»:{' '}
                                            <strong className="text-gray-700">
                                                {(selectedSurvey?.questions || []).filter((question) => question?.allow_other).length}
                                            </strong>
                                        </div>
                                    </div>
                                )}

                                {/* Tabs for manager */}
                                {canManage && (
                                    <div className="flex gap-1 mt-3">
                                        {['questions', 'stats'].map((tab) => (
                                            <button
                                                key={tab}
                                                onClick={() => setActiveTab(tab)}
                                                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                                                    activeTab === tab
                                                        ? 'bg-blue-600 text-white shadow-sm'
                                                        : 'text-gray-500 hover:bg-gray-100'
                                                }`}
                                            >
                                                {tab === 'questions' ? <><FaIcon className="fas fa-question-circle mr-1" />Вопросы</> : <><FaIcon className="fas fa-chart-bar mr-1" />Статистика</>}
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Detail body */}
                            <div className="flex-1 overflow-y-auto p-5 space-y-3">

                                {/* Время теста истекло, а попытка не отправлена */}
                                {isOperator && isTestTimeOver && !isSelectedSurveyCompleted && (() => {
                                    const hasSavedAttempt = (selectedSurvey?.my_assignment?.draft_answers || []).length > 0;
                                    return (
                                        <div className={`rounded-xl border p-4 ${
                                            hasSavedAttempt ? 'border-slate-200 bg-slate-50' : 'border-amber-200 bg-amber-50'
                                        }`}>
                                            <div className="flex items-start gap-3">
                                                <FaIcon className={`fas fa-hourglass-end mt-0.5 ${
                                                    hasSavedAttempt ? 'text-slate-400' : 'text-amber-500'
                                                }`} />
                                                <div className={`text-sm ${hasSavedAttempt ? 'text-slate-700' : 'text-amber-800'}`}>
                                                    <div className="font-semibold">Время теста истекло</div>
                                                    <div className="mt-1 text-[13px]">
                                                        {hasSavedAttempt
                                                            ? 'Выбранные ответы сохранены — система отправит их сама, результат появится в течение минуты.'
                                                            : 'Тест остался непройденным: попытка не начиналась.'}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    );
                                })()}

                                {/* Operator fills out survey */}
                                {isOperator && canFillSelectedSurvey && (
                                    <div className="space-y-3">
                                        {selectedSurvey?.is_test && testMsLeft != null && (
                                            <div className={`sticky top-0 z-10 -mx-1 flex items-center justify-between gap-3 rounded-xl px-3.5 py-2.5 backdrop-blur ${
                                                testMsLeft <= 60000
                                                    ? 'bg-red-50/90 ring-1 ring-red-200'
                                                    : 'bg-slate-50/90 ring-1 ring-slate-200/70'
                                            }`}>
                                                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                                                    <FaIcon className={`fas fa-stopwatch text-[11px] ${testMsLeft <= 60000 ? 'text-red-500' : 'text-slate-400'}`} />
                                                    До завершения теста
                                                </div>
                                                <div className={`text-[15px] font-semibold tabular-nums ${testMsLeft <= 60000 ? 'text-red-600' : 'text-slate-800'}`}>
                                                    {formatCountdown(testMsLeft)}
                                                </div>
                                            </div>
                                        )}
                                        {(selectedSurvey.questions || []).map((question, index) => {
                                            const answer = answers[question.id] || {};
                                            return (
                                                <div key={question.id} className="border border-gray-200 rounded-xl p-4 space-y-3">
                                                    <div className="flex items-start justify-between gap-2">
                                                        <div>
                                                            <div className="text-[11px] text-gray-400 mb-1">
                                                                #{index + 1} · {questionTypeLabel(question.type)}
                                                                {question.required && <span className="text-red-400 ml-1">*</span>}
                                                            </div>
                                                            <div className="text-sm font-medium text-gray-800">{question.text}</div>
                                                        </div>
                                                        {selectedSurvey?.is_test && question.points != null && (
                                                            <span
                                                                className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium tabular-nums text-slate-500"
                                                                title={question.partial_credit
                                                                    ? 'Балл начисляется за угаданные варианты, но один лишний вариант обнуляет вопрос'
                                                                    : 'Балл только за все правильные варианты сразу'}
                                                            >
                                                                {formatPoints(question.points)} балл.
                                                                {question.partial_credit && ' · частично'}
                                                            </span>
                                                        )}
                                                    </div>

                                                    {question.type === 'rating' ? (
                                                        <div className="flex items-center gap-2">
                                                            {[1, 2, 3, 4, 5].map((value) => {
                                                                const active = Number(answer.rating_value) === value;
                                                                return (
                                                                    <button
                                                                        key={`${question.id}_${value}`}
                                                                        type="button"
                                                                        onClick={() => updateAnswer(question.id, { rating_value: value })}
                                                                        className={`w-10 h-10 rounded-xl font-semibold text-sm border-2 transition-all ${
                                                                            active
                                                                                ? 'bg-amber-500 text-white border-amber-500 shadow-sm scale-105'
                                                                                : 'border-gray-200 text-gray-500 hover:border-amber-300 hover:text-amber-500'
                                                                        }`}
                                                                    >
                                                                        {value}
                                                                    </button>
                                                                );
                                                            })}
                                                        </div>
                                                    ) : (
                                                        <div className="space-y-1.5">
                                                            {(question.options || []).map((option) => {
                                                                const selected = Array.isArray(answer.selected_options) && answer.selected_options.includes(option);
                                                                return (
                                                                    <label
                                                                        key={`${question.id}_${option}`}
                                                                        className={`flex items-center gap-3 p-2.5 rounded-lg cursor-pointer border transition-all ${
                                                                            selected ? 'border-blue-200 bg-blue-50' : 'border-transparent hover:bg-gray-50'
                                                                        }`}
                                                                    >
                                                                        <div className={`w-4 h-4 shrink-0 flex items-center justify-center transition-all ${
                                                                            question.type === 'single'
                                                                                ? `rounded-full border-2 ${selected ? 'border-blue-600' : 'border-gray-300'}`
                                                                                : `rounded border-2 ${selected ? 'bg-blue-600 border-blue-600' : 'border-gray-300'}`
                                                                        }`}>
                                                                            {selected && question.type === 'single' && <div className="w-1.5 h-1.5 rounded-full bg-blue-600" />}
                                                                            {selected && question.type === 'multiple' && <FaIcon className="fas fa-check text-white text-[8px]" />}
                                                                        </div>
                                                                        <input
                                                                            type={question.type === 'single' ? 'radio' : 'checkbox'}
                                                                            className="hidden"
                                                                            name={`q_${question.id}`}
                                                                            checked={selected}
                                                                            onChange={() => {
                                                                                if (question.type === 'single') {
                                                                                    updateAnswer(question.id, { selected_options: [option], answer_text: '' });
                                                                                }
                                                                                else {
                                                                                    const set = new Set(answer.selected_options || []);
                                                                                    if (set.has(option)) set.delete(option);
                                                                                    else set.add(option);
                                                                                    updateAnswer(question.id, { selected_options: Array.from(set) });
                                                                                }
                                                                            }}
                                                                        />
                                                                        <span className="text-sm text-gray-700">{option}</span>
                                                                    </label>
                                                                );
                                                            })}
                                                            {question.allow_other && (
                                                                <div className="space-y-1">
                                                                    <input
                                                                        value={answer.answer_text || ''}
                                                                        onChange={(e) => {
                                                                            const nextText = String(e.target.value || '').slice(0, OTHER_ANSWER_MAX_LENGTH);
                                                                            if (question.type === 'single') {
                                                                                updateAnswer(question.id, {
                                                                                    answer_text: nextText,
                                                                                    selected_options: nextText ? [] : (answer.selected_options || [])
                                                                                });
                                                                                return;
                                                                            }
                                                                            updateAnswer(question.id, { answer_text: nextText });
                                                                        }}
                                                                        maxLength={OTHER_ANSWER_MAX_LENGTH}
                                                                        placeholder="Другое..."
                                                                        className={inputCls}
                                                                    />
                                                                    <div className="text-[10px] text-gray-400 text-right">
                                                                        {String(answer.answer_text || '').length}/{OTHER_ANSWER_MAX_LENGTH}
                                                                    </div>
                                                                </div>
                                                            )}
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}

                                        <button
                                            onClick={submitSurvey}
                                            disabled={isSubmitting}
                                            className="w-full py-3 rounded-xl bg-emerald-600 text-white font-medium text-sm hover:bg-emerald-700 disabled:opacity-50 transition-all shadow-sm active:scale-[0.98]"
                                        >
                                            {isSubmitting
                                                ? <><FaIcon className="fas fa-spinner fa-spin mr-2" />Отправка...</>
                                                : <><FaIcon className="fas fa-paper-plane mr-2" />{selectedSurvey?.is_test ? 'Завершить тест' : 'Завершить опрос'}</>}
                                        </button>
                                    </div>
                                )}

                                {/* Manager questions tab */}
                                {!isOperator && (!canManage || activeTab === 'questions') && (
                                    <div className="space-y-2">
                                        {(selectedSurvey.questions || []).length === 0 && (
                                            <div className="rounded-xl border border-gray-100 bg-gray-50 p-4 text-sm text-gray-400">
                                                В этом опросе нет сохраненных вопросов.
                                            </div>
                                        )}
                                        {(selectedSurvey.questions || []).map((question, index) => {
                                            const normalizedOptions = toUniqueTrimmedList(question.options);
                                            return (
                                                <div key={question.id} className="flex gap-3 items-start p-3 rounded-xl border border-gray-100 bg-gray-50/60">
                                                    <div className="w-6 h-6 rounded-lg bg-blue-50 flex items-center justify-center shrink-0 mt-0.5">
                                                        <span className="text-[10px] font-bold text-blue-500">{index + 1}</span>
                                                    </div>
                                                    <div className="flex-1">
                                                        <div className="text-sm font-medium text-gray-800">{question.text}</div>
                                                        <div className="flex items-center gap-2 mt-1">
                                                            <Badge color="gray">{questionTypeLabel(question.type)}</Badge>
                                                            {question.required && <Badge color="blue">Обязательный</Badge>}
                                                        </div>
                                                        {question.type !== 'rating' && (
                                                            <div className="mt-2 space-y-1">
                                                                <div className="text-[11px] text-gray-400">Варианты ответа</div>
                                                                {normalizedOptions.length > 0 ? (
                                                                    <div className="flex flex-wrap gap-1.5">
                                                                        {normalizedOptions.map((option) => (
                                                                            <span key={`${question.id}_${option}`} className="inline-flex items-center px-2 py-0.5 rounded bg-white border border-gray-200 text-xs text-gray-700">
                                                                                {option}
                                                                            </span>
                                                                        ))}
                                                                        {question.allow_other && (
                                                                            <span className="inline-flex items-center px-2 py-0.5 rounded bg-amber-50 border border-amber-200 text-xs text-amber-700">
                                                                                Другое
                                                                            </span>
                                                                        )}
                                                                    </div>
                                                                ) : (
                                                                    <div className="text-xs text-gray-500">
                                                                        {question.allow_other ? 'Только поле «Другое»' : 'Без фиксированных вариантов'}
                                                                    </div>
                                                                )}
                                                            </div>
                                                        )}
                                                        {selectedSurvey?.is_test && question.type !== 'rating' && (
                                                            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                                                                <span className="text-emerald-700">
                                                                    Правильный ответ: {toUniqueTrimmedList(question.correct_options).length > 0
                                                                        ? toUniqueTrimmedList(question.correct_options).join(', ')
                                                                        : '—'}
                                                                </span>
                                                                <span className="text-gray-500 tabular-nums">
                                                                    Баллы: {formatPoints(question.points)}
                                                                </span>
                                                                {question.partial_credit && (
                                                                    <span className="text-gray-500">Частичный зачёт</span>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                            );
                                        })}
                                    </div>
                                )}

                                {/* Completed operator view */}
                                {isOperator && isSelectedSurveyCompleted && !isRetakingSelectedTest && (
                                    <div className="space-y-3">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <div className="text-xs text-gray-500">
                                                Отправлено:{' '}
                                                <strong className="text-gray-700 tabular-nums">
                                                    {formatSurveyDateTime(selectedSurvey?.my_response?.submitted_at || selectedSurvey?.my_assignment?.submitted_at)}
                                                </strong>
                                            </div>
                                            {canRetakeSelectedTest && (
                                                <button
                                                    type="button"
                                                    onClick={() => setRetakeSurveyId(selectedSurvey.id)}
                                                    className="inline-flex items-center gap-1.5 rounded-xl bg-slate-100 px-3 py-1.5 text-[12.5px] font-medium text-slate-700 transition-colors hover:bg-slate-200 active:scale-[0.98]"
                                                >
                                                    <FaIcon className="fas fa-redo text-[10px]" />
                                                    Пройти ещё раз
                                                </button>
                                            )}
                                        </div>
                                        {selectedSurvey?.is_test && (() => {
                                            const summary = selectedSurvey?.my_response?.test_summary || {};
                                            const percent = Number(summary.score_percent || 0);
                                            return (
                                                <div className={`${iosCard} p-4`}>
                                                    <div className="flex items-baseline justify-between gap-3">
                                                        <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                            Результат теста
                                                        </div>
                                                        {summary.is_auto_submitted && (
                                                            <Badge color="amber">Автоотправка по времени</Badge>
                                                        )}
                                                    </div>
                                                    <div className="mt-1.5 text-[26px] font-semibold tabular-nums leading-none text-slate-900">
                                                        {percent.toFixed(1).replace(/\.0$/, '')}%
                                                    </div>
                                                    <div className="mt-2.5">
                                                        <ProgressBar
                                                            value={percent}
                                                            color={percent >= 80 ? 'emerald' : (percent >= 60 ? 'blue' : 'amber')}
                                                        />
                                                    </div>
                                                    <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[12.5px] text-slate-500">
                                                        <span>
                                                            Баллы:{' '}
                                                            <strong className="tabular-nums text-slate-800">
                                                                {formatPoints(summary.earned_points)} / {formatPoints(summary.max_points)}
                                                            </strong>
                                                        </span>
                                                        <span>
                                                            Верных ответов:{' '}
                                                            <strong className="tabular-nums text-slate-800">
                                                                {Number(summary.correct_answers || 0)} / {Number(summary.total_questions || 0)}
                                                            </strong>
                                                        </span>
                                                    </div>
                                                    {selectedTestInfo?.affects_quality && (
                                                        <div className="mt-3 text-[12px] text-slate-400">
                                                            Результат учтён в качестве оператора как «Тестирование знаний».
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })()}
                                        {(selectedSurvey.questions || []).map((question, index) => {
                                            const answersByQuestion = selectedSurvey?.my_response?.answers_by_question || {};
                                            const answer = answersByQuestion[String(question.id)] || answersByQuestion[question.id] || null;
                                            const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
                                            const hasAnswer = question.type === 'rating'
                                                ? Number.isFinite(Number(answer?.rating_value))
                                                : (selectedOptions.length > 0 || String(answer?.answer_text || '').trim().length > 0);
                                            const expectedOptions = getExpectedOptionsForTest(question, answer);
                                            const answerStatus = selectedSurvey?.is_test
                                                ? testAnswerStatusMeta(question, answer, hasAnswer)
                                                : null;
                                            const earnedPoints = Number(answer?.earned_points);
                                            return (
                                                <div key={question.id} className="p-3 rounded-xl border border-gray-100 bg-gray-50/60 space-y-2">
                                                    <div className="flex items-start gap-3">
                                                        <div className="w-6 h-6 rounded-lg bg-blue-50 flex items-center justify-center shrink-0 mt-0.5">
                                                            <span className="text-[10px] font-bold text-blue-500">{index + 1}</span>
                                                        </div>
                                                        <div className="flex-1 min-w-0">
                                                            <div className="text-sm font-medium text-gray-800">{question.text}</div>
                                                            <div className="flex flex-wrap items-center gap-2 mt-1">
                                                                <Badge color="gray">{questionTypeLabel(question.type)}</Badge>
                                                                {question.required && <Badge color="blue">Обязательный</Badge>}
                                                                {answerStatus && (
                                                                    <Badge color={answerStatus.color}>{answerStatus.label}</Badge>
                                                                )}
                                                                {selectedSurvey?.is_test && Number.isFinite(earnedPoints) && (
                                                                    <span className="text-[11px] tabular-nums text-slate-500">
                                                                        {formatPoints(earnedPoints)} / {formatPoints(question.points)} балл.
                                                                    </span>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <div className="ml-9 text-sm text-gray-700">
                                                        <span className="text-gray-500 mr-1">Ваш ответ:</span>
                                                        <strong className="font-medium text-gray-800 break-words">
                                                            {formatQuestionAnswerText(question, answer)}
                                                        </strong>
                                                    </div>
                                                    {/* Правильный ответ показываем только когда он открыт:
                                                        при разрешённом повторе внутри окна это была бы подсказка. */}
                                                    {selectedSurvey?.is_test && expectedOptions.length > 0 && (
                                                        <div className="ml-9 text-sm text-gray-700">
                                                            <span className="text-gray-500 mr-1">Правильный ответ:</span>
                                                            <strong className="font-medium text-emerald-700 break-words">
                                                                {expectedOptions.join(', ')}
                                                            </strong>
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                    </div>
                                )}

                                {/* Manager stats tab */}
                                {canManage && activeTab === 'stats' && (
                                    <div className="space-y-3">
                                        {displayQuestionStats.length === 0 && (
                                            <div className="text-center py-8">
                                                <FaIcon className="fas fa-chart-bar text-gray-200 text-3xl mb-2 block" />
                                                <p className="text-sm text-gray-400">Данных для статистики пока нет</p>
                                            </div>
                                        )}
                                        {displayQuestionStats.map((stat, index) => renderDetailedQuestionStats(stat, index))}

                                        <div className="border border-gray-100 rounded-xl p-4 bg-white space-y-3">
                                            <div className="flex items-center justify-between gap-3 flex-wrap">
                                                <div>
                                                    <h4 className="text-sm font-semibold text-gray-800">
                                                        {isTestStatsSurvey && statsViewMode === 'scores' ? 'Общий балл сотрудников' : 'Ответы сотрудников'}
                                                    </h4>
                                                    <p className="text-[11px] text-gray-500 mt-0.5">
                                                        {isTestStatsSurvey && statsViewMode === 'scores'
                                                            ? 'Сводная таблица по результатам теста для каждого сотрудника.'
                                                            : 'Табличный просмотр: что выбрал и что написал каждый сотрудник.'}
                                                    </p>
                                                </div>
                                                <div className="flex items-center gap-2 flex-wrap">
                                                    {isTestStatsSurvey && (
                                                        <div className="inline-flex rounded-lg border border-gray-200 p-0.5 bg-gray-50">
                                                            <button
                                                                type="button"
                                                                onClick={() => setStatsViewMode('scores')}
                                                                className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                                                                    statsViewMode === 'scores'
                                                                        ? 'bg-blue-600 text-white'
                                                                        : 'text-gray-600 hover:bg-gray-100'
                                                                }`}
                                                            >
                                                                Общий балл
                                                            </button>
                                                            <button
                                                                type="button"
                                                                onClick={() => setStatsViewMode('answers')}
                                                                className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                                                                    statsViewMode === 'answers'
                                                                        ? 'bg-blue-600 text-white'
                                                                        : 'text-gray-600 hover:bg-gray-100'
                                                                }`}
                                                            >
                                                                Ответы
                                                            </button>
                                                        </div>
                                                    )}
                                                    <button
                                                        type="button"
                                                        onClick={exportSurveyStatsExcel}
                                                        disabled={isStatsExporting || !selectedSurvey?.id}
                                                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                                        title="Выгрузить статистику в Excel"
                                                    >
                                                        <FaIcon className={`fas ${isStatsExporting ? 'fa-spinner fa-spin' : 'fa-file-excel'}`} />
                                                        {isStatsExporting ? 'Экспорт...' : 'Excel'}
                                                    </button>
                                                    <Badge color="blue">
                                                        {detailedStatsRows.length}/{departmentFilteredDetailedStatsRows.length}
                                                    </Badge>
                                                </div>
                                            </div>

                                            <div className="relative max-w-sm">
                                                <FaIcon className="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-gray-300 text-xs" />
                                                <input
                                                    value={statsOperatorQuery}
                                                    onChange={(e) => setStatsOperatorQuery(e.target.value)}
                                                    placeholder="Поиск по сотруднику"
                                                    className={`${inputCls} pl-8 py-2`}
                                                />
                                            </div>

                                            <div className="overflow-x-auto border border-gray-100 rounded-lg">
                                                {isTestStatsSurvey && statsViewMode === 'scores' ? (
                                                    <table className="min-w-full divide-y divide-gray-100 text-xs">
                                                        <thead className="bg-gray-50">
                                                            <tr>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Сотрудник</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Статус</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Начало</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Завершение</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Повтор</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Баллы</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Итоговая оценка</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">В качество</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Верно</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Отвечено</th>
                                                            </tr>
                                                        </thead>
                                                        <tbody className="divide-y divide-gray-100">
                                                            {detailedStatsRows.length === 0 && (
                                                                <tr>
                                                                    <td className="px-3 py-4 text-center text-gray-400" colSpan={10}>
                                                                        Сотрудники не найдены
                                                                    </td>
                                                                </tr>
                                                            )}
                                                            {detailedStatsRows.map((row) => {
                                                                const isCompleted = String(row?.status || '').toLowerCase() === 'completed';
                                                                const repeatIteration = Number(
                                                                    row?.repeat_iteration != null
                                                                        ? row.repeat_iteration
                                                                        : (selectedSurvey?.repeat?.iteration || 1)
                                                                );
                                                                const repeatSurveyId = Number(row?.repeat_survey_id || selectedSurvey?.id || 0);
                                                                const testSummary = row?.test_summary || {};
                                                                const totalQuestions = Number(testSummary?.total_questions || 0);
                                                                const answeredQuestions = Number(testSummary?.answered_questions || 0);
                                                                const correctAnswers = Number(testSummary?.correct_answers || 0);
                                                                const scoreRaw = testSummary?.score_percent;
                                                                const hasScore = (
                                                                    scoreRaw !== null
                                                                    && scoreRaw !== undefined
                                                                    && `${scoreRaw}`.trim() !== ''
                                                                    && Number.isFinite(Number(scoreRaw))
                                                                );
                                                                const scoreValue = hasScore ? Number(scoreRaw) : 0;
                                                                return (
                                                                    <tr key={`stats_score_row_${row?.operator_id}_${repeatSurveyId}`}>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-800 font-medium">
                                                                            <div className="flex items-center gap-1.5">
                                                                                <span>{row?.operator_name || `#${row?.operator_id || '—'}`}</span>
                                                                                {row?.is_operator_dismissed && <Badge color="amber">Уволен</Badge>}
                                                                            </div>
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap">
                                                                            <div className="flex items-center gap-1.5">
                                                                                <Badge color={isCompleted ? 'green' : 'amber'}>
                                                                                    {isCompleted ? 'Пройден' : 'Назначен'}
                                                                                </Badge>
                                                                                {testSummary?.is_auto_submitted && (
                                                                                    <Badge color="amber">по времени</Badge>
                                                                                )}
                                                                            </div>
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-600 tabular-nums">
                                                                            {formatSurveyDateTime(row?.started_at)}
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-600 tabular-nums">
                                                                            {formatSurveyDateTime(row?.submitted_at)}
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap">
                                                                            <Badge color={repeatIteration > 1 ? 'blue' : 'gray'}>
                                                                                #{repeatIteration}
                                                                            </Badge>
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-700 tabular-nums">
                                                                            {hasScore
                                                                                ? `${formatPoints(testSummary?.earned_points)} / ${formatPoints(testSummary?.max_points)}`
                                                                                : <span className="text-gray-400">—</span>}
                                                                        </td>
                                                                        <td className="px-3 py-2.5 align-top">
                                                                            {hasScore ? (
                                                                                <div className="min-w-[140px] space-y-1">
                                                                                    <div className="text-sm font-semibold text-gray-800 tabular-nums">
                                                                                        {scoreValue.toFixed(1).replace(/\.0$/, '')}%
                                                                                    </div>
                                                                                    <ProgressBar
                                                                                        value={scoreValue}
                                                                                        color={scoreValue >= 80 ? 'emerald' : (scoreValue >= 60 ? 'blue' : 'amber')}
                                                                                    />
                                                                                </div>
                                                                            ) : (
                                                                                <span className="text-gray-400">—</span>
                                                                            )}
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap">
                                                                            {testSummary?.sent_to_quality ? (
                                                                                <span className="inline-flex items-center gap-1 text-emerald-700">
                                                                                    <FaIcon className="fas fa-check text-[10px]" />
                                                                                    Тестирование знаний
                                                                                </span>
                                                                            ) : (
                                                                                <span className="text-gray-400">—</span>
                                                                            )}
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-700 tabular-nums">
                                                                            {totalQuestions > 0 ? `${correctAnswers}/${totalQuestions}` : '—'}
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-700 tabular-nums">
                                                                            {totalQuestions > 0 ? `${answeredQuestions}/${totalQuestions}` : '—'}
                                                                        </td>
                                                                    </tr>
                                                                );
                                                            })}
                                                        </tbody>
                                                    </table>
                                                ) : (
                                                    <table className="min-w-full divide-y divide-gray-100 text-xs">
                                                        <thead className="bg-gray-50">
                                                            <tr>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Сотрудник</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Статус</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Отправлено</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Повтор</th>
                                                                {(selectedSurvey?.questions || []).map((question, qIndex) => (
                                                                    <th key={`table_q_${question.id}`} className="px-3 py-2 text-left font-semibold text-gray-600 min-w-[260px]">
                                                                        <div className="text-[10px] text-gray-400 mb-0.5">Вопрос #{qIndex + 1}</div>
                                                                        <div className="line-clamp-2">{question.text}</div>
                                                                    </th>
                                                                ))}
                                                            </tr>
                                                        </thead>
                                                        <tbody className="divide-y divide-gray-100">
                                                            {detailedStatsRows.length === 0 && (
                                                                <tr>
                                                                    <td
                                                                        className="px-3 py-4 text-center text-gray-400"
                                                                        colSpan={4 + (selectedSurvey?.questions || []).length}
                                                                    >
                                                                        Сотрудники не найдены
                                                                    </td>
                                                                </tr>
                                                            )}
                                                            {detailedStatsRows.map((row) => {
                                                                const isCompleted = String(row?.status || '').toLowerCase() === 'completed';
                                                                const repeatIteration = Number(
                                                                    row?.repeat_iteration != null
                                                                        ? row.repeat_iteration
                                                                        : (selectedSurvey?.repeat?.iteration || 1)
                                                                );
                                                                const repeatSurveyId = Number(row?.repeat_survey_id || selectedSurvey?.id || 0);
                                                                return (
                                                                    <tr key={`stats_row_${row?.operator_id}_${repeatSurveyId}`}>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-800 font-medium">
                                                                            <div className="flex items-center gap-1.5">
                                                                                <span>{row?.operator_name || `#${row?.operator_id || '—'}`}</span>
                                                                                {row?.is_operator_dismissed && <Badge color="amber">Уволен</Badge>}
                                                                            </div>
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap">
                                                                            <Badge color={isCompleted ? 'green' : 'amber'}>
                                                                                {isCompleted ? 'Пройден' : 'Назначен'}
                                                                            </Badge>
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-600">
                                                                            {formatSurveyDateTime(row?.submitted_at)}
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap">
                                                                            <Badge color={repeatIteration > 1 ? 'blue' : 'gray'}>
                                                                                #{repeatIteration}
                                                                            </Badge>
                                                                        </td>
                                                                        {(selectedSurvey?.questions || []).map((question, questionIndex) => {
                                                                            const resolved = resolveStatsQuestionAndAnswer(row, question, questionIndex);
                                                                            const hasAnswer = hasSurveyAnswer(resolved.question, resolved.answer);
                                                                            const isCorrect = isTestStatsSurvey ? isTestAnswerCorrect(resolved.question, resolved.answer) : false;
                                                                            const expectedOptions = isTestStatsSurvey ? getExpectedOptionsForTest(resolved.question, resolved.answer) : [];
                                                                            const answerStatus = isTestStatsSurvey
                                                                                ? testAnswerStatusMeta(resolved.question, resolved.answer, hasAnswer)
                                                                                : null;
                                                                            const answerEarnedPoints = Number(resolved.answer?.earned_points);
                                                                            const answerCellClass = (
                                                                                isTestStatsSurvey && hasAnswer
                                                                                    ? (isCorrect
                                                                                        ? 'bg-emerald-50/70'
                                                                                        : (answerStatus?.color === 'blue' ? 'bg-blue-50/70' : 'bg-amber-50/70'))
                                                                                    : ''
                                                                            );
                                                                            return (
                                                                                <td key={`stats_row_${row?.operator_id}_${repeatSurveyId}_q_${question.id}`} className={`px-3 py-2.5 align-top text-gray-700 ${answerCellClass}`}>
                                                                                    <div className={`max-w-[300px] break-words ${isTestStatsSurvey && hasAnswer && isCorrect ? 'text-emerald-800 font-medium' : ''}`}>
                                                                                        {formatQuestionAnswerText(resolved.question, resolved.answer)}
                                                                                    </div>
                                                                                    {isTestStatsSurvey && (
                                                                                        <div className="mt-1 space-y-1">
                                                                                            <div className="flex flex-wrap items-center gap-1.5">
                                                                                                <Badge color={answerStatus.color}>{answerStatus.label}</Badge>
                                                                                                {Number.isFinite(answerEarnedPoints) && (
                                                                                                    <span className="text-[10px] tabular-nums text-slate-500">
                                                                                                        {formatPoints(answerEarnedPoints)} / {formatPoints(resolved.question?.points)}
                                                                                                    </span>
                                                                                                )}
                                                                                            </div>
                                                                                            {expectedOptions.length > 0 && (
                                                                                                <div className="text-[10px] text-emerald-700 break-words">
                                                                                                    Правильный: {expectedOptions.join(', ')}
                                                                                                </div>
                                                                                            )}
                                                                                        </div>
                                                                                    )}
                                                                                </td>
                                                                            );
                                                                        })}
                                                                    </tr>
                                                                );
                                                            })}
                                                        </tbody>
                                                    </table>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default SurveysView;
