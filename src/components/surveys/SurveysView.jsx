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
    correctOptions: []
});

const emptyDraft = () => ({
    title: '',
    description: '',
    isTest: false,
    directionIds: [],
    tenureWeeksMin: '',
    tenureWeeksMax: '',
    operatorIds: [],
    questions: [emptyQuestion()]
});

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

/* ─── main component ─── */

const SurveysView = ({ user, operators = [], directions = [], showToast, apiBaseUrl, onSurveyProgressChanged }) => {
    const [surveys, setSurveys] = useState([]);
    const [selectedSurveyId, setSelectedSurveyId] = useState('');
    const [showBuilder, setShowBuilder] = useState(false);
    const [repeatSourceSurveyId, setRepeatSourceSurveyId] = useState(null);
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
    const showToastRef = useRef(showToast);
    const onSurveyProgressChangedRef = useRef(onSurveyProgressChanged);

    const canManage = isManagerRole(user?.role);
    const isOperator = normalizeRole(user?.role) === 'operator';
    const isRepeatMode = repeatSourceSurveyId != null;

    useEffect(() => { showToastRef.current = showToast; }, [showToast]);
    useEffect(() => { onSurveyProgressChangedRef.current = onSurveyProgressChanged; }, [onSurveyProgressChanged]);

    const notify = useCallback((message, type = 'success') => {
        if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
    }, []);

    const headers = useMemo(
        () => ({ 'X-API-Key': user?.apiKey, 'X-User-Id': user?.id }),
        [user?.apiKey, user?.id]
    );

    const directionNameById = useMemo(() => {
        const map = new Map();
        (directions || []).forEach((direction) => {
            const id = direction?.id != null ? String(direction.id) : null;
            const name = direction?.name || direction?.title || direction?.direction_name || 'Без направления';
            if (id) map.set(id, name);
        });
        return map;
    }, [directions]);

    const normalizedOperators = useMemo(() => {
        return (operators || [])
            .map((operator) => {
                const id = Number(operator?.id);
                if (!Number.isFinite(id)) return null;
                const status = String(operator?.status || '').trim().toLowerCase();
                const statusPeriodCode = String(operator?.status_period_status_code || '').trim().toLowerCase();
                if (isDismissedOperatorStatus(status) || isDismissedOperatorStatus(statusPeriodCode)) return null;
                const directionId = operator?.direction_id != null ? String(operator.direction_id) : 'none';
                const weeks = getTenureWeeks(operator?.hire_date);
                return {
                    id,
                    name: String(operator?.name || `#${id}`),
                    directionId,
                    directionName: operator?.direction || directionNameById.get(directionId) || 'Без направления',
                    tenureWeeks: weeks,
                    tenureLabel: tenureLabel(weeks)
                };
            })
            .filter(Boolean)
            .sort((a, b) => a.name.localeCompare(b.name, 'ru', { sensitivity: 'base' }));
    }, [operators, directionNameById]);

    const filteredOperators = useMemo(() => {
        const query = operatorQuery.trim().toLowerCase();
        const selectedDirections = new Set((draft.directionIds || []).map(String));
        const minWeeks = parseWeeksInput(draft.tenureWeeksMin);
        const maxWeeks = parseWeeksInput(draft.tenureWeeksMax);
        return normalizedOperators.filter((operator) => {
            const byDirection = selectedDirections.size === 0 || selectedDirections.has(operator.directionId);
            const byQuery = !query || operator.name.toLowerCase().includes(query) || operator.directionName.toLowerCase().includes(query);
            const hasTenure = Number.isFinite(operator.tenureWeeks);
            const byMin = minWeeks == null || (hasTenure && operator.tenureWeeks >= minWeeks);
            const byMax = maxWeeks == null || (hasTenure && operator.tenureWeeks <= maxWeeks);
            return byDirection && byQuery && byMin && byMax;
        });
    }, [draft.directionIds, draft.tenureWeeksMax, draft.tenureWeeksMin, normalizedOperators, operatorQuery]);

    const filteredOperatorIds = useMemo(
        () => filteredOperators.map((operator) => Number(operator.id)).filter(Number.isFinite),
        [filteredOperators]
    );
    const filteredOperatorIdSet = useMemo(() => new Set(filteredOperatorIds), [filteredOperatorIds]);
    const selectedFilteredOperatorsCount = useMemo(
        () => (draft.operatorIds || []).reduce(
            (count, id) => count + (filteredOperatorIdSet.has(Number(id)) ? 1 : 0),
            0
        ),
        [draft.operatorIds, filteredOperatorIdSet]
    );
    const hasFilteredOperators = filteredOperatorIds.length > 0;
    const allFilteredOperatorsSelected = hasFilteredOperators && selectedFilteredOperatorsCount === filteredOperatorIds.length;

    const selectAllFilteredOperators = useCallback(() => {
        setDraft((prev) => {
            const nextSelected = new Set((prev.operatorIds || []).map((id) => Number(id)).filter(Number.isFinite));
            filteredOperatorIds.forEach((id) => nextSelected.add(id));
            return { ...prev, operatorIds: Array.from(nextSelected) };
        });
    }, [filteredOperatorIds]);

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
            if (typeof onSurveyProgressChangedRef.current === 'function') {
                onSurveyProgressChangedRef.current();
            }
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось загрузить опросы', 'error');
        } finally {
            setIsLoading(false);
        }
    }, [apiBaseUrl, headers, notify, user?.id]);

    useEffect(() => { loadSurveys(); }, [loadSurveys]);

    useEffect(() => {
        if (!selectedSurveyId && surveys[0]?.id) setSelectedSurveyId(surveys[0].id);
        if (selectedSurveyId && !surveys.some((item) => String(item.id) === String(selectedSurveyId))) {
            setSelectedSurveyId(surveys[0]?.id || '');
        }
    }, [selectedSurveyId, surveys]);

    useEffect(() => {
        setStatsOperatorQuery('');
        const currentSurvey = (surveys || []).find((item) => String(item.id) === String(selectedSurveyId));
        setStatsViewMode(currentSurvey?.is_test ? 'scores' : 'answers');
    }, [selectedSurveyId, surveys]);

    const selectedSurvey = useMemo(
        () => surveys.find((item) => String(item.id) === String(selectedSurveyId)) || null,
        [selectedSurveyId, surveys]
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

    const detailedStatsRows = useMemo(() => {
        const rows = detailedStatsSourceRows;
        const query = String(statsOperatorQuery || '').trim().toLowerCase();
        if (!query) return rows;
        return rows.filter((row) => {
            const name = String(row?.operator_name || '').toLowerCase();
            const idText = String(row?.operator_id || '');
            return name.includes(query) || idText.includes(query);
        });
    }, [detailedStatsSourceRows, statsOperatorQuery]);

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

    const hasSurveyAnswer = useCallback((question, answer) => {
        if (!question || !answer) return false;
        if (question.type === 'rating') {
            return Number.isFinite(Number(answer?.rating_value));
        }
        const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
        const answerText = String(answer?.answer_text || '').trim();
        return selectedOptions.length > 0 || answerText.length > 0;
    }, []);

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
        const initial = {};
        (selectedSurvey.questions || []).forEach((question) => {
            initial[question.id] = { selected_options: [], answer_text: '', rating_value: '' };
        });
        setAnswers(initial);
    }, [isOperator, selectedSurveyId, selectedSurvey]);

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
        setAnswers((prev) => ({ ...prev, [questionId]: { ...(prev[questionId] || {}), ...patch } }));
    };

    const resetBuilder = useCallback(() => {
        setRepeatSourceSurveyId(null);
        setDraft(emptyDraft());
        setOperatorQuery('');
    }, []);

    const closeBuilder = useCallback(() => {
        setShowBuilder(false);
        resetBuilder();
    }, [resetBuilder]);

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
                    correctOptions: type === QUESTION_TYPE_OTHER_ONLY ? [] : toUniqueTrimmedList(question?.correct_options)
                };
            })
            : [emptyQuestion()];

        setDraft({
            title: String(survey?.title || ''),
            description: String(survey?.description || ''),
            isTest: !!survey?.is_test,
            directionIds: (survey?.assignment?.direction_ids || []).map((id) => String(id)).filter(Boolean),
            tenureWeeksMin: survey?.assignment?.tenure_weeks_min != null ? String(survey.assignment.tenure_weeks_min) : '',
            tenureWeeksMax: survey?.assignment?.tenure_weeks_max != null ? String(survey.assignment.tenure_weeks_max) : '',
            operatorIds: (survey?.assignment?.operator_ids || []).map((id) => Number(id)).filter(Number.isFinite),
            questions: clonedQuestions
        });
        setOperatorQuery('');
        setRepeatSourceSurveyId(sourceId);
        setShowBuilder(true);
    }, [canManage]);

    const createSurvey = async () => {
        if (!String(draft.title || '').trim()) return notify('Укажите название опроса', 'error');
        if (!(draft.operatorIds || []).length) return notify('Выберите минимум одного оператора', 'error');
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
            return {
                text: String(question.text || '').trim(),
                type: payloadType,
                required: !!question.required,
                allow_other: draft.isTest ? false : (payloadType === 'rating' ? false : (isOtherOnlyQuestion ? true : !!question.allowOther)),
                options: normalizedOptions,
                correct_options: normalizedCorrectOptions
            };
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
            }
        }

        const payload = {
            title: String(draft.title || '').trim(),
            description: String(draft.description || '').trim(),
            is_test: !!draft.isTest,
            assignment: {
                direction_ids: (draft.directionIds || []).map((id) => Number(id)).filter(Number.isFinite),
                tenure_weeks_min: minWeeks,
                tenure_weeks_max: maxWeeks,
                operator_ids: (draft.operatorIds || []).map((id) => Number(id)).filter(Number.isFinite)
            },
            questions: normalizedQuestions
        };
        if (isRepeatMode) {
            payload.repeat_from_survey_id = Number(repeatSourceSurveyId);
        }

        setIsSaving(true);
        try {
            await axios.post(`${apiBaseUrl}/api/surveys`, payload, { headers });
            notify('Опрос создан', 'success');
            closeBuilder();
            await loadSurveys();
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось создать опрос', 'error');
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
        if (!selectedSurvey || !selectedSurvey?.my_assignment?.can_submit) return;
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
            await axios.post(`${apiBaseUrl}/api/surveys/${selectedSurvey.id}/submit`, { answers: preparedAnswers }, { headers });
            notify('Опрос успешно пройден', 'success');
            await loadSurveys();
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось отправить ответы', 'error');
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
                    )}
                </div>
            </div>

            {/* ── Survey Builder ── */}
            {canManage && showBuilder && (
                <div className="bg-white rounded-2xl border border-blue-100 shadow-sm">
                    <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between gap-3 flex-wrap">
                        <div className="w-6 h-6 rounded-lg bg-blue-50 flex items-center justify-center">
                            <FaIcon className="fas fa-pencil-alt text-blue-500 text-xs" />
                        </div>
                        <span className="font-semibold text-gray-800 text-sm">Новый опрос</span>
                    </div>

                    <div className="p-6 space-y-6">

                        {/* Basic info */}
                        <div className="space-y-3">
                            <SectionTitle>Основное</SectionTitle>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                <FormField label="Название опроса *">
                                    <input
                                        value={draft.title}
                                        onChange={(e) => setDraft((p) => ({ ...p, title: e.target.value }))}
                                        placeholder="Например: Опрос удовлетворённости"
                                        className={inputCls}
                                    />
                                </FormField>
                                <FormField label="Описание">
                                    <input
                                        value={draft.description}
                                        onChange={(e) => setDraft((p) => ({ ...p, description: e.target.value }))}
                                        placeholder="Краткое описание (необязательно)"
                                        className={inputCls}
                                    />
                                </FormField>
                            </div>
                            <label className="inline-flex items-center gap-2 text-xs text-gray-600">
                                <input
                                    type="checkbox"
                                    checked={!!draft.isTest}
                                    onChange={(e) => toggleTestMode(e.target.checked)}
                                    className="rounded border-gray-300"
                                />
                                Тест (с правильными и неправильными ответами)
                            </label>
                            {draft.isTest && (
                                <div className="text-[11px] text-amber-600">
                                    В тесте недоступны вопросы типа рейтинг и вариант «Другое».
                                </div>
                            )}
                        </div>

                        {/* Filters */}
                        <div className="space-y-3">
                            <SectionTitle>Фильтры назначения</SectionTitle>
                            <div className="grid grid-cols-2 gap-3">
                                <FormField label="Стаж от (недель)">
                                    <input
                                        type="number" min="0"
                                        value={draft.tenureWeeksMin}
                                        onChange={(e) => setDraft((p) => ({ ...p, tenureWeeksMin: e.target.value }))}
                                        placeholder="Минимум"
                                        className={inputCls}
                                    />
                                </FormField>
                                <FormField label="Стаж до (недель)">
                                    <input
                                        type="number" min="0"
                                        value={draft.tenureWeeksMax}
                                        onChange={(e) => setDraft((p) => ({ ...p, tenureWeeksMax: e.target.value }))}
                                        placeholder="Максимум"
                                        className={inputCls}
                                    />
                                </FormField>
                            </div>

                            {directionNameById.size > 0 && (
                                <FormField label="Направления">
                                    <div className="flex flex-wrap gap-1.5">
                                        {Array.from(directionNameById.entries()).map(([id, name]) => {
                                            const active = draft.directionIds.includes(id);
                                            return (
                                                <button
                                                    key={id}
                                                    onClick={() => toggleArrayValue(setDraft, 'directionIds', id)}
                                                    className={`px-3 py-1 rounded-full text-xs font-medium border transition-all ${
                                                        active
                                                            ? 'bg-blue-600 text-white border-blue-600 shadow-sm'
                                                            : 'bg-white text-gray-600 border-gray-200 hover:border-blue-300 hover:text-blue-600'
                                                    }`}
                                                >
                                                    {name}
                                                </button>
                                            );
                                        })}
                                    </div>
                                </FormField>
                            )}
                        </div>

                        {/* Operators */}
                        <div className="space-y-2">
                            <div className="flex items-center justify-between">
                                <SectionTitle>Операторы *</SectionTitle>
                                {draft.operatorIds.length > 0 && (
                                    <Badge color="blue">{draft.operatorIds.length} выбрано</Badge>
                                )}
                            </div>
                            <div className="relative">
                                <FaIcon className="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-gray-300 text-xs" />
                                <input
                                    value={operatorQuery}
                                    onChange={(e) => setOperatorQuery(e.target.value)}
                                    placeholder="Поиск по имени или направлению"
                                    className={`${inputCls} pl-8`}
                                />
                            </div>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="text-[11px] text-gray-500">
                                    По фильтрам: {selectedFilteredOperatorsCount}/{filteredOperatorIds.length} выбрано
                                </div>
                                <div className="flex flex-wrap items-center gap-2">
                                    <button
                                        type="button"
                                        onClick={selectAllFilteredOperators}
                                        disabled={!hasFilteredOperators || allFilteredOperatorsSelected}
                                        className="px-2.5 py-1 text-xs rounded-lg border border-blue-200 text-blue-600 hover:bg-blue-50 disabled:opacity-40 disabled:cursor-not-allowed"
                                    >
                                        Выбрать всех по фильтрам
                                    </button>
                                    <button
                                        type="button"
                                        onClick={clearFilteredOperators}
                                        disabled={!hasFilteredOperators || selectedFilteredOperatorsCount === 0}
                                        className="px-2.5 py-1 text-xs rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                                    >
                                        Снять по фильтрам
                                    </button>
                                </div>
                            </div>
                            <div className="max-h-48 overflow-y-auto border border-gray-100 rounded-xl divide-y divide-gray-50 bg-white">
                                {filteredOperators.length === 0 && (
                                    <div className="p-3 text-xs text-gray-400 text-center">Операторы не найдены</div>
                                )}
                                {filteredOperators.map((operator) => {
                                    const checked = draft.operatorIds.includes(operator.id);
                                    return (
                                        <label
                                            key={operator.id}
                                            className={`flex items-center gap-3 px-3 py-2.5 cursor-pointer transition-colors ${checked ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                                        >
                                            <div className={`w-4 h-4 rounded flex items-center justify-center shrink-0 border transition-all ${checked ? 'bg-blue-600 border-blue-600' : 'border-gray-300'}`}>
                                                {checked && <FaIcon className="fas fa-check text-white text-[9px]" />}
                                            </div>
                                            <input type="checkbox" className="hidden" checked={checked} onChange={() => toggleArrayValue(setDraft, 'operatorIds', operator.id)} />
                                            <span className="text-sm font-medium text-gray-800 flex-1">{operator.name}</span>
                                            <span className="text-xs text-gray-400">{operator.directionName}</span>
                                            <span className="text-xs text-gray-400">{operator.tenureLabel}</span>
                                        </label>
                                    );
                                })}
                            </div>
                        </div>

                        {/* Questions */}
                        <div className="space-y-3">
                            <SectionTitle>Вопросы ({draft.questions.length})</SectionTitle>
                            <div className="space-y-3">
                                {draft.questions.map((question, index) => {
                                    const options = Array.isArray(question.options) ? question.options : [];
                                    const availableQuestionTypes = draft.isTest
                                        ? QUESTION_TYPES.filter((item) => item.value !== 'rating' && item.value !== QUESTION_TYPE_OTHER_ONLY)
                                        : QUESTION_TYPES;
                                    return (
                                        <div key={question.id} className="border border-gray-200 rounded-xl p-4 space-y-3 bg-gray-50/50">
                                            <div className="flex items-center justify-between">
                                                <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Вопрос #{index + 1}</span>
                                                <button
                                                    disabled={draft.questions.length <= 1}
                                                    onClick={() => setDraft((p) => ({ ...p, questions: p.questions.filter((item) => item.id !== question.id) }))}
                                                    className="text-xs px-2 py-1 rounded-lg bg-red-50 text-red-500 hover:bg-red-100 disabled:opacity-30 transition-colors"
                                                >
                                                    <FaIcon className="fas fa-trash-alt mr-1" />Удалить
                                                </button>
                                            </div>

                                            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                                                <div className="md:col-span-2">
                                                    <input
                                                        value={question.text}
                                                        onChange={(e) => updateQuestion(question.id, { text: e.target.value })}
                                                        placeholder="Текст вопроса"
                                                        className={inputCls}
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
                                                            correctOptions: nextType === QUESTION_TYPE_OTHER_ONLY ? [] : nextCorrectOptions
                                                        });
                                                    }}
                                                    className={inputCls}
                                                >
                                                    {availableQuestionTypes.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                                                </select>
                                            </div>

                                            {question.type !== 'rating' && (
                                                <div className="space-y-2 pl-1">
                                                    <div className="text-[11px] text-gray-400 font-medium">Варианты ответа</div>
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
                                                                        className={`w-5 h-5 shrink-0 flex items-center justify-center border-2 transition-all ${
                                                                            question.type === 'single' ? 'rounded-full' : 'rounded'
                                                                        } ${
                                                                            isCorrectOption
                                                                                ? 'bg-emerald-500 border-emerald-500 text-white'
                                                                                : 'border-gray-300 text-transparent hover:border-emerald-400'
                                                                        }`}
                                                                        title={isCorrectOption ? 'Правильный вариант' : 'Отметить как правильный'}
                                                                    >
                                                                        <FaIcon className="fas fa-check text-[9px]" />
                                                                    </button>
                                                                ) : (
                                                                    <div className="w-5 h-5 rounded-full border-2 border-gray-200 shrink-0" />
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
                                                                    className={inputCls}
                                                                />
                                                                <button
                                                                    type="button"
                                                                    disabled={options.length <= 2}
                                                                    onClick={() => removeQuestionOption(question.id, optionIndex)}
                                                                    className="text-gray-300 hover:text-red-400 disabled:opacity-20 transition-colors px-1"
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
                                                        className="text-xs text-blue-500 hover:text-blue-700 transition-colors ml-7"
                                                    >
                                                        <FaIcon className="fas fa-plus mr-1" />Добавить вариант
                                                    </button>
                                                    )}

                                                    {draft.isTest ? (
                                                        <div className="text-[11px] text-emerald-600 ml-7">
                                                            Отметьте правильные варианты слева от текста ответа.
                                                        </div>
                                                    ) : question.type === QUESTION_TYPE_OTHER_ONLY ? (
                                                        <div className="text-[11px] text-gray-500 ml-7">
                                                            Для этого типа доступно только поле "Другое" без фиксированных вариантов.
                                                        </div>
                                                    ) : (
                                                        <label className="inline-flex items-center gap-2 text-xs text-gray-500 ml-7">
                                                            <input
                                                                type="checkbox"
                                                                checked={!!question.allowOther}
                                                                onChange={(e) => updateQuestion(question.id, { allowOther: e.target.checked })}
                                                                className="rounded border-gray-300"
                                                            />
                                                            Разрешить вариант "Другое"
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
                                className="w-full py-2.5 border-2 border-dashed border-gray-200 rounded-xl text-sm text-gray-400 hover:border-blue-300 hover:text-blue-500 transition-all"
                            >
                                <FaIcon className="fas fa-plus mr-2" />Добавить вопрос
                            </button>
                        </div>

                        {/* Save */}
                        <div className="flex justify-end pt-2 border-t border-gray-100">
                            <button
                                onClick={createSurvey}
                                disabled={isSaving}
                                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-all shadow-sm"
                            >
                                {isSaving
                                    ? <><FaIcon className="fas fa-spinner fa-spin" />Сохранение...</>
                                    : <><FaIcon className="fas fa-check" />{draft.isTest ? 'Сохранить тест' : 'Сохранить опрос'}</>}
                            </button>
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
                        {surveys.length > 0 && <Badge color="gray">{surveys.length}</Badge>}
                    </div>

                    <div className="flex-1 overflow-y-auto divide-y divide-gray-50">
                        {isLoading && (
                            <div className="p-6 flex items-center justify-center gap-2 text-sm text-gray-400">
                                <FaIcon className="fas fa-spinner fa-spin" />Загрузка...
                            </div>
                        )}
                        {!isLoading && surveys.length === 0 && (
                            <div className="p-8 text-center">
                                <div className="w-12 h-12 rounded-2xl bg-gray-50 flex items-center justify-center mx-auto mb-3">
                                    <FaIcon className="fas fa-clipboard-list text-gray-300 text-xl" />
                                </div>
                                <p className="text-sm text-gray-400">
                                    {isOperator ? 'Назначенных опросов пока нет' : 'Опросов пока нет'}
                                </p>
                            </div>
                        )}
                        {!isLoading && surveys.map((survey) => {
                            const isSelected = String(survey.id) === String(selectedSurveyId);
                            const isCompleted = survey?.my_assignment?.status === 'completed';
                            const completionRate = survey?.statistics?.completion_rate || 0;
                            const repeatIteration = Number(survey?.repeat?.iteration || 1);
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
                                            <div className="mt-1.5">
                                                {canManage ? (
                                                    <div className="space-y-1">
                                                        <div className="flex items-center justify-between text-[11px] text-gray-500">
                                                            <span>Пройдено: {survey?.statistics?.completed_count || 0} из {survey?.statistics?.assigned_count || 0}</span>
                                                            <span>{completionRate}%</span>
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
                                        <div className="flex items-center gap-2">
                                            <h3 className="text-base font-bold text-gray-900">{selectedSurvey.title}</h3>
                                            {selectedSurvey?.is_test && <Badge color="green">Тест</Badge>}
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
                                        <button
                                            type="button"
                                            onClick={() => startRepeatSurvey(selectedSurvey)}
                                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors"
                                            title="Создать повтор опроса"
                                        >
                                            <FaIcon className="fas fa-redo" />
                                            Повторить
                                        </button>
                                    )}
                                    {isOperator && (
                                        <Badge color={selectedSurvey?.my_assignment?.status === 'completed' ? 'green' : 'amber'}>
                                            {selectedSurvey?.my_assignment?.status === 'completed' ? 'Пройден' : 'Назначен'}
                                        </Badge>
                                    )}
                                </div>

                                {/* Meta row */}
                                {(!canManage || activeTab === 'stats') && (
                                    <div className="flex flex-wrap gap-2 mt-3">
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                            <FaIcon className="fas fa-users text-gray-400 text-[10px]" />
                                            Операторов: <strong className="text-gray-700">{selectedSurvey?.assignment?.operator_ids?.length || 0}</strong>
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
                                                    Пройдено: <strong className="text-gray-700">{selectedSurvey?.statistics?.completed_count || 0} / {selectedSurvey?.statistics?.assigned_count || 0}</strong>
                                                </div>
                                                <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 rounded-lg px-2.5 py-1.5">
                                                    <FaIcon className="fas fa-hourglass-half text-gray-400 text-[10px]" />
                                                    Ожидают: <strong className="text-gray-700">{selectedSurvey?.statistics?.pending_count || 0}</strong>
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

                                {/* Operator fills out survey */}
                                {isOperator && selectedSurvey?.my_assignment?.can_submit && (
                                    <div className="space-y-3">
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
                                            className="w-full py-3 rounded-xl bg-emerald-600 text-white font-medium text-sm hover:bg-emerald-700 disabled:opacity-50 transition-all shadow-sm"
                                        >
                                            {isSubmitting
                                                ? <><FaIcon className="fas fa-spinner fa-spin mr-2" />Отправка...</>
                                                : <><FaIcon className="fas fa-paper-plane mr-2" />Завершить опрос</>}
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
                                                            <div className="mt-1 text-xs text-emerald-700">
                                                                Правильный ответ: {toUniqueTrimmedList(question.correct_options).length > 0
                                                                    ? toUniqueTrimmedList(question.correct_options).join(', ')
                                                                    : '—'}
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                            );
                                        })}
                                    </div>
                                )}

                                {/* Completed operator view */}
                                {isOperator && selectedSurvey?.my_assignment?.status === 'completed' && (
                                    <div className="space-y-3">
                                        <div className="text-xs text-gray-500">
                                            Отправлено:{' '}
                                            <strong className="text-gray-700">
                                                {formatSurveyDateTime(selectedSurvey?.my_response?.submitted_at || selectedSurvey?.my_assignment?.submitted_at)}
                                            </strong>
                                        </div>
                                        {selectedSurvey?.is_test && (
                                            <div className="rounded-xl border border-emerald-100 bg-emerald-50 p-3">
                                                <div className="text-xs text-emerald-700">
                                                    Результат теста
                                                </div>
                                                <div className="text-sm font-semibold text-emerald-800 mt-1">
                                                    {Number(selectedSurvey?.my_response?.test_summary?.score_percent || 0).toFixed(1).replace(/\.0$/, '')}%
                                                </div>
                                                <div className="text-xs text-emerald-700 mt-1">
                                                    Верных ответов: {Number(selectedSurvey?.my_response?.test_summary?.correct_answers || 0)}
                                                    {' '}из{' '}
                                                    {Number(selectedSurvey?.my_response?.test_summary?.total_questions || 0)}
                                                </div>
                                            </div>
                                        )}
                                        {(selectedSurvey.questions || []).map((question, index) => {
                                            const answersByQuestion = selectedSurvey?.my_response?.answers_by_question || {};
                                            const answer = answersByQuestion[String(question.id)] || answersByQuestion[question.id] || null;
                                            const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
                                            const hasAnswer = question.type === 'rating'
                                                ? Number.isFinite(Number(answer?.rating_value))
                                                : (selectedOptions.length > 0 || String(answer?.answer_text || '').trim().length > 0);
                                            const expectedOptions = getExpectedOptionsForTest(question, answer);
                                            const isCorrect = selectedSurvey?.is_test ? isTestAnswerCorrect(question, answer) : false;
                                            return (
                                                <div key={question.id} className="p-3 rounded-xl border border-gray-100 bg-gray-50/60 space-y-2">
                                                    <div className="flex items-start gap-3">
                                                        <div className="w-6 h-6 rounded-lg bg-blue-50 flex items-center justify-center shrink-0 mt-0.5">
                                                            <span className="text-[10px] font-bold text-blue-500">{index + 1}</span>
                                                        </div>
                                                        <div className="flex-1 min-w-0">
                                                            <div className="text-sm font-medium text-gray-800">{question.text}</div>
                                                            <div className="flex items-center gap-2 mt-1">
                                                                <Badge color="gray">{questionTypeLabel(question.type)}</Badge>
                                                                {question.required && <Badge color="blue">Обязательный</Badge>}
                                                                {selectedSurvey?.is_test && (
                                                                    <Badge color={!hasAnswer ? 'gray' : (isCorrect ? 'green' : 'amber')}>
                                                                        {!hasAnswer ? 'Нет ответа' : (isCorrect ? 'Верно' : 'Неверно')}
                                                                    </Badge>
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
                                                    {selectedSurvey?.is_test && (
                                                        <div className="ml-9 text-sm text-gray-700">
                                                            <span className="text-gray-500 mr-1">Правильный ответ:</span>
                                                            <strong className="font-medium text-emerald-700 break-words">
                                                                {expectedOptions.length > 0 ? expectedOptions.join(', ') : '—'}
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
                                        {(selectedSurvey?.statistics?.question_stats || []).length === 0 && (
                                            <div className="text-center py-8">
                                                <FaIcon className="fas fa-chart-bar text-gray-200 text-3xl mb-2 block" />
                                                <p className="text-sm text-gray-400">Данных для статистики пока нет</p>
                                            </div>
                                        )}
                                        {(selectedSurvey?.statistics?.question_stats || []).map((stat, index) => renderDetailedQuestionStats(stat, index))}

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
                                                        {detailedStatsRows.length}/{detailedStatsSourceRows.length}
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
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Отправлено</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Повтор</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Общий балл</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Верно</th>
                                                                <th className="px-3 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">Отвечено</th>
                                                            </tr>
                                                        </thead>
                                                        <tbody className="divide-y divide-gray-100">
                                                            {detailedStatsRows.length === 0 && (
                                                                <tr>
                                                                    <td className="px-3 py-4 text-center text-gray-400" colSpan={7}>
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
                                                                            {row?.operator_name || `#${row?.operator_id || '—'}`}
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
                                                                        <td className="px-3 py-2.5 align-top">
                                                                            {hasScore ? (
                                                                                <div className="min-w-[140px] space-y-1">
                                                                                    <div className="text-sm font-semibold text-gray-800">
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
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-700">
                                                                            {totalQuestions > 0 ? `${correctAnswers}/${totalQuestions}` : '—'}
                                                                        </td>
                                                                        <td className="px-3 py-2.5 whitespace-nowrap text-gray-700">
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
                                                                            {row?.operator_name || `#${row?.operator_id || '—'}`}
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
                                                                            const answerCellClass = (
                                                                                isTestStatsSurvey && hasAnswer
                                                                                    ? (isCorrect ? 'bg-emerald-50/70' : 'bg-amber-50/70')
                                                                                    : ''
                                                                            );
                                                                            return (
                                                                                <td key={`stats_row_${row?.operator_id}_${repeatSurveyId}_q_${question.id}`} className={`px-3 py-2.5 align-top text-gray-700 ${answerCellClass}`}>
                                                                                    <div className={`max-w-[300px] break-words ${isTestStatsSurvey && hasAnswer && isCorrect ? 'text-emerald-800 font-medium' : ''}`}>
                                                                                        {formatQuestionAnswerText(resolved.question, resolved.answer)}
                                                                                    </div>
                                                                                    {isTestStatsSurvey && (
                                                                                        <div className="mt-1 space-y-1">
                                                                                            <Badge color={!hasAnswer ? 'gray' : (isCorrect ? 'green' : 'amber')}>
                                                                                                {!hasAnswer ? 'Нет ответа' : (isCorrect ? 'Верно' : 'Неверно')}
                                                                                            </Badge>
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
