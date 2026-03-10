import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';

const QUESTION_TYPES = [
    { value: 'single', label: 'Один вариант' },
    { value: 'multiple', label: 'Несколько вариантов' },
    { value: 'rating', label: 'Рейтинг 1-5' }
];

const isManagerRole = (role) => ['admin', 'sv', 'supervisor', 'trainer'].includes(String(role || '').toLowerCase());
const questionTypeLabel = (type) => QUESTION_TYPES.find((item) => item.value === type)?.label || type;
const parseWeeksInput = (value) => {
    if (value === '' || value == null) return null;
    const number = Number(value);
    if (!Number.isFinite(number)) return null;
    return Math.max(0, Math.floor(number));
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
    options: ['', '']
});

const emptyDraft = () => ({
    title: '',
    description: '',
    directionIds: [],
    tenureWeeksMin: '',
    tenureWeeksMax: '',
    operatorIds: [],
    questions: [emptyQuestion()]
});

const SurveysView = ({ user, operators = [], directions = [], showToast, apiBaseUrl }) => {
    const [surveys, setSurveys] = useState([]);
    const [selectedSurveyId, setSelectedSurveyId] = useState('');
    const [showBuilder, setShowBuilder] = useState(false);
    const [draft, setDraft] = useState(emptyDraft);
    const [operatorQuery, setOperatorQuery] = useState('');
    const [answers, setAnswers] = useState({});
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const showToastRef = useRef(showToast);

    const canManage = isManagerRole(user?.role);
    const isOperator = String(user?.role || '').toLowerCase() === 'operator';

    useEffect(() => {
        showToastRef.current = showToast;
    }, [showToast]);

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

    const loadSurveys = useCallback(async () => {
        if (!apiBaseUrl || !user?.id) return;
        setIsLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/surveys`, { headers });
            setSurveys(Array.isArray(response?.data?.surveys) ? response.data.surveys : []);
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось загрузить опросы', 'error');
        } finally {
            setIsLoading(false);
        }
    }, [apiBaseUrl, headers, notify, user?.id]);

    useEffect(() => {
        loadSurveys();
    }, [loadSurveys]);

    useEffect(() => {
        if (!selectedSurveyId && surveys[0]?.id) setSelectedSurveyId(surveys[0].id);
        if (selectedSurveyId && !surveys.some((item) => String(item.id) === String(selectedSurveyId))) {
            setSelectedSurveyId(surveys[0]?.id || '');
        }
    }, [selectedSurveyId, surveys]);

    const selectedSurvey = useMemo(
        () => surveys.find((item) => String(item.id) === String(selectedSurveyId)) || null,
        [selectedSurveyId, surveys]
    );

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
                if (question.id !== questionId || question.type === 'rating') return question;
                const options = Array.isArray(question.options) ? question.options : [];
                return { ...question, options: [...options, ''] };
            })
        }));
    };

    const removeQuestionOption = (questionId, optionIndex) => {
        setDraft((prev) => ({
            ...prev,
            questions: prev.questions.map((question) => {
                if (question.id !== questionId || question.type === 'rating') return question;
                const options = Array.isArray(question.options) ? question.options : [];
                if (options.length <= 2) return question;
                return { ...question, options: options.filter((_, idx) => idx !== optionIndex) };
            })
        }));
    };

    const updateAnswer = (questionId, patch) => {
        setAnswers((prev) => ({ ...prev, [questionId]: { ...(prev[questionId] || {}), ...patch } }));
    };

    const createSurvey = async () => {
        if (!String(draft.title || '').trim()) return notify('Укажите название опроса', 'error');
        if (!(draft.operatorIds || []).length) return notify('Выберите минимум одного оператора', 'error');
        const minWeeks = parseWeeksInput(draft.tenureWeeksMin);
        const maxWeeks = parseWeeksInput(draft.tenureWeeksMax);
        if (minWeeks != null && maxWeeks != null && minWeeks > maxWeeks) return notify('Минимальный стаж не может быть больше максимального', 'error');

        for (let i = 0; i < draft.questions.length; i += 1) {
            const question = draft.questions[i];
            if (!String(question.text || '').trim()) return notify(`Заполните текст вопроса #${i + 1}`, 'error');
            if (question.type !== 'rating' && (question.options || []).map((option) => String(option || '').trim()).filter(Boolean).length < 2) {
                return notify(`Нужно минимум 2 варианта в вопросе #${i + 1}`, 'error');
            }
        }

        const payload = {
            title: String(draft.title || '').trim(),
            description: String(draft.description || '').trim(),
            assignment: {
                direction_ids: (draft.directionIds || []).map((id) => Number(id)).filter(Number.isFinite),
                tenure_weeks_min: minWeeks,
                tenure_weeks_max: maxWeeks,
                operator_ids: (draft.operatorIds || []).map((id) => Number(id)).filter(Number.isFinite)
            },
            questions: (draft.questions || []).map((question) => ({
                text: String(question.text || '').trim(),
                type: question.type,
                required: !!question.required,
                allow_other: question.type === 'rating' ? false : !!question.allowOther,
                options: question.type === 'rating' ? [] : (question.options || []).map((option) => String(option || '').trim()).filter(Boolean)
            }))
        };

        setIsSaving(true);
        try {
            await axios.post(`${apiBaseUrl}/api/surveys`, payload, { headers });
            notify('Опрос создан', 'success');
            setDraft(emptyDraft());
            setOperatorQuery('');
            setShowBuilder(false);
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
            if (question.type === 'rating') payload.rating_value = answer.rating_value === '' ? null : Number(answer.rating_value);
            else {
                payload.selected_options = Array.isArray(answer.selected_options) ? answer.selected_options : [];
                if (String(answer.answer_text || '').trim()) payload.answer_text = String(answer.answer_text || '').trim();
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

        const answeredCount = Number(stat.responses_with_answer || 0);
        const respondentsTotal = Number(
            stat.respondents_total != null
                ? stat.respondents_total
                : selectedSurvey?.statistics?.responses_count || 0
        );
        const skippedCount = Number(
            stat.skipped_count != null
                ? stat.skipped_count
                : Math.max(0, respondentsTotal - answeredCount)
        );
        const responseRate = Number.isFinite(Number(stat.response_rate))
            ? Number(stat.response_rate)
            : (respondentsTotal > 0 ? (answeredCount / respondentsTotal) * 100 : 0);

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
            <div key={`${selectedSurvey?.id || 'survey'}_stat_${index}`} className="text-xs text-gray-700 border border-gray-200 rounded-lg p-3 space-y-2">
                <div className="flex items-start justify-between gap-2">
                    <div className="font-medium text-gray-800">Вопрос #{index + 1}</div>
                    <div className="text-[11px] text-gray-500">
                        Ответили: {answeredCount} из {respondentsTotal} ({formatPercent(responseRate)})
                    </div>
                </div>
                <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                    <div className="h-full bg-blue-500" style={{ width: percentToWidth(responseRate) }} />
                </div>
                <div className="text-[11px] text-gray-500">Пропустили: {skippedCount}</div>

                {stat.type === 'rating' && (
                    <div className="space-y-1">
                        <div className="text-[11px] text-gray-600">
                            Средний рейтинг: <strong>{stat.average_rating ?? '-'}</strong>
                            {' | '}
                            Медиана: <strong>{stat.median_rating ?? '-'}</strong>
                            {' | '}
                            Диапазон: <strong>{stat.min_rating ?? '-'}-{stat.max_rating ?? '-'}</strong>
                        </div>
                        <div className="space-y-1">
                            {ratingDistribution.map((bucket) => {
                                const value = Number(bucket.value);
                                const count = Number(bucket.count || 0);
                                const percentAnswers = Number(bucket.percent_of_answers || 0);
                                return (
                                    <div key={`${selectedSurvey?.id || 'survey'}_stat_${index}_rating_${value}`} className="space-y-0.5">
                                        <div className="flex items-center justify-between gap-2 text-[11px]">
                                            <span>{value} ★</span>
                                            <span>{count} ({formatPercent(percentAnswers)})</span>
                                        </div>
                                        <div className="h-1.5 bg-amber-100 rounded-full overflow-hidden">
                                            <div className="h-full bg-amber-500" style={{ width: percentToWidth(percentAnswers) }} />
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {stat.type !== 'rating' && (
                    <div className="space-y-1">
                        {stat.type === 'multiple' && (
                            <div className="text-[11px] text-gray-500">
                                Всего выборов: <strong>{Number(stat.selections_total || 0)}</strong> (можно выбрать несколько вариантов)
                            </div>
                        )}

                        {options.length === 0 && <div className="text-[11px] text-gray-500">Данных по вариантам пока нет.</div>}

                        {options.map((option, optionIndex) => {
                            const optionLabel = String(option?.option || `Вариант ${optionIndex + 1}`);
                            const optionCount = Number(option?.count || 0);
                            const percentRespondents = Number(
                                option?.percent_of_respondents != null
                                    ? option.percent_of_respondents
                                    : option?.percent || 0
                            );
                            const percentAnswers = Number(
                                option?.percent_of_answers != null
                                    ? option.percent_of_answers
                                    : option?.percent || 0
                            );
                            return (
                                <div key={`${selectedSurvey?.id || 'survey'}_stat_${index}_option_${optionIndex}`} className="space-y-0.5">
                                    <div className="flex items-center justify-between gap-2 text-[11px]">
                                        <span className="truncate" title={optionLabel}>{optionLabel}</span>
                                        <span>{optionCount} ({formatPercent(percentRespondents)})</span>
                                    </div>
                                    <div className="h-1.5 bg-blue-100 rounded-full overflow-hidden">
                                        <div className="h-full bg-blue-500" style={{ width: percentToWidth(percentRespondents) }} />
                                    </div>
                                    <div className="text-[10px] text-gray-500">
                                        От ответивших на вопрос: {formatPercent(percentAnswers)}
                                    </div>
                                </div>
                            );
                        })}

                        {topOptions.length > 0 && (
                            <div className="text-[11px] text-gray-500">
                                Топ варианты: {topOptions.map((option) => `${option.option} (${option.count})`).join(', ')}
                            </div>
                        )}
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="space-y-6">
            <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div>
                        <h2 className="text-2xl font-semibold text-gray-800 flex items-center gap-2"><FaIcon className="fas fa-list-alt text-blue-600" />Опросы</h2>
                        <p className="text-sm text-gray-600">{canManage ? 'Назначение по стажу в неделях, направлению и операторам.' : 'Назначенные вам опросы.'}</p>
                    </div>
                    {canManage && <button onClick={() => setShowBuilder((value) => !value)} className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700">{showBuilder ? 'Закрыть' : 'Создать опрос'}</button>}
                </div>
            </div>

            {canManage && showBuilder && (
                <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <input value={draft.title} onChange={(event) => setDraft((prev) => ({ ...prev, title: event.target.value }))} placeholder="Название опроса" className="p-3 border border-gray-300 rounded-lg" />
                        <input value={draft.description} onChange={(event) => setDraft((prev) => ({ ...prev, description: event.target.value }))} placeholder="Описание (необязательно)" className="p-3 border border-gray-300 rounded-lg" />
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <input type="number" min="0" value={draft.tenureWeeksMin} onChange={(event) => setDraft((prev) => ({ ...prev, tenureWeeksMin: event.target.value }))} placeholder="Стаж от, недель" className="p-2.5 border border-gray-300 rounded-lg" />
                        <input type="number" min="0" value={draft.tenureWeeksMax} onChange={(event) => setDraft((prev) => ({ ...prev, tenureWeeksMax: event.target.value }))} placeholder="Стаж до, недель" className="p-2.5 border border-gray-300 rounded-lg" />
                    </div>
                    <div className="flex flex-wrap gap-2">{Array.from(directionNameById.entries()).map(([id, name]) => <button key={id} onClick={() => toggleArrayValue(setDraft, 'directionIds', id)} className={`px-3 py-1 rounded-full border text-sm ${draft.directionIds.includes(id) ? 'bg-blue-600 text-white border-blue-600' : 'border-gray-300'}`}>{name}</button>)}</div>
                    <input value={operatorQuery} onChange={(event) => setOperatorQuery(event.target.value)} placeholder="Поиск оператора" className="w-full p-2.5 border border-gray-300 rounded-lg" />
                    <div className="max-h-44 overflow-y-auto border border-gray-200 rounded-lg divide-y divide-gray-100">{filteredOperators.map((operator) => <label key={operator.id} className="flex items-center gap-2 p-2 text-sm"><input type="checkbox" checked={draft.operatorIds.includes(operator.id)} onChange={() => toggleArrayValue(setDraft, 'operatorIds', operator.id)} /><span className="font-medium">{operator.name}</span><span className="text-gray-500">| {operator.directionName} | {operator.tenureLabel}</span></label>)}</div>
                    <div className="space-y-2">
                        {draft.questions.map((question, index) => {
                            const options = Array.isArray(question.options) ? question.options : [];
                            return (
                                <div key={question.id} className="border border-gray-200 rounded-lg p-3 space-y-2">
                                    <div className="flex items-center justify-between">
                                        <span className="text-xs text-gray-500">Вопрос #{index + 1}</span>
                                        <button
                                            disabled={draft.questions.length <= 1}
                                            onClick={() => setDraft((prev) => ({ ...prev, questions: prev.questions.filter((item) => item.id !== question.id) }))}
                                            className="text-xs px-2 py-1 bg-red-50 text-red-600 rounded"
                                        >
                                            Удалить
                                        </button>
                                    </div>
                                    <input
                                        value={question.text}
                                        onChange={(event) => updateQuestion(question.id, { text: event.target.value })}
                                        placeholder="Текст вопроса"
                                        className="w-full p-2 border border-gray-300 rounded-lg"
                                    />
                                    <select
                                        value={question.type}
                                        onChange={(event) =>
                                            updateQuestion(question.id, {
                                                type: event.target.value,
                                                allowOther: event.target.value === 'rating' ? false : question.allowOther,
                                                options: event.target.value === 'rating' ? [] : (question.options?.length ? question.options : ['', ''])
                                            })
                                        }
                                        className="w-full p-2 border border-gray-300 rounded-lg"
                                    >
                                        {QUESTION_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
                                    </select>
                                    {question.type !== 'rating' && (
                                        <div className="space-y-1">
                                            {options.map((option, optionIndex) => (
                                                <div key={`${question.id}_${optionIndex}`} className="flex items-center gap-2">
                                                    <input
                                                        value={option}
                                                        onChange={(event) =>
                                                            updateQuestion(question.id, {
                                                                options: options.map((current, idx) => (idx === optionIndex ? event.target.value : current))
                                                            })
                                                        }
                                                        placeholder={`Вариант ${optionIndex + 1}`}
                                                        className="w-full p-2 border border-gray-300 rounded-lg"
                                                    />
                                                    <button
                                                        type="button"
                                                        disabled={options.length <= 2}
                                                        onClick={() => removeQuestionOption(question.id, optionIndex)}
                                                        className="text-xs px-2 py-1 bg-red-50 text-red-600 rounded disabled:opacity-50"
                                                    >
                                                        Удалить
                                                    </button>
                                                </div>
                                            ))}
                                            <button
                                                type="button"
                                                onClick={() => addQuestionOption(question.id)}
                                                className="text-xs px-2 py-1 rounded bg-gray-100 hover:bg-gray-200"
                                            >
                                                + Вариант
                                            </button>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                    <div className="flex gap-2"><button onClick={() => setDraft((prev) => ({ ...prev, questions: [...prev.questions, emptyQuestion()] }))} className="px-3 py-2 rounded bg-gray-100">+ Вопрос</button><button onClick={createSurvey} disabled={isSaving} className="px-4 py-2 rounded bg-green-600 text-white disabled:opacity-50">{isSaving ? 'Сохранение...' : 'Сохранить'}</button></div>
                </div>
            )}

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
                    <div className="px-4 py-3 border-b border-gray-100 font-semibold text-gray-800">Список опросов</div>
                    <div className="p-4 space-y-2 max-h-[560px] overflow-y-auto">
                        {isLoading && <div className="text-sm text-gray-500">Загрузка...</div>}
                        {!isLoading && surveys.length === 0 && (
                            <div className="text-sm text-gray-500">
                                {isOperator ? 'Назначенных опросов пока нет.' : 'Опросов пока нет.'}
                            </div>
                        )}
                        {!isLoading && surveys.map((survey) => (
                            <div
                                key={survey.id}
                                className={`border rounded-lg p-3 ${String(survey.id) === String(selectedSurveyId) ? 'border-blue-400 bg-blue-50' : 'border-gray-200'}`}
                            >
                                <div className="flex items-start justify-between gap-2">
                                    <button onClick={() => setSelectedSurveyId(survey.id)} className="text-left flex-1">
                                        <div className="font-semibold text-gray-800">{survey.title}</div>
                                        <div className="text-xs text-gray-500 mt-1">
                                            {canManage
                                                ? `Назначено: ${survey?.statistics?.assigned_count || 0} | Пройдено: ${survey?.statistics?.completed_count || 0} (${survey?.statistics?.completion_rate || 0}%)`
                                                : `Статус: ${survey?.my_assignment?.status === 'completed' ? 'Пройден' : 'Назначен'}`}
                                        </div>
                                    </button>
                                    {canManage && (
                                        <button onClick={() => removeSurvey(survey.id)} className="text-xs px-2 py-1 rounded bg-red-50 text-red-600">
                                            Удалить
                                        </button>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
                    <div className="px-4 py-3 border-b border-gray-100 font-semibold text-gray-800">Детали опроса</div>
                    <div className="p-4 space-y-3 max-h-[560px] overflow-y-auto">
                        {!selectedSurvey && <div className="text-sm text-gray-500">Выберите опрос слева.</div>}

                        {selectedSurvey && (
                            <>
                                <div>
                                    <div className="text-xl font-semibold text-gray-900">{selectedSurvey.title}</div>
                                    {selectedSurvey.description && <div className="text-sm text-gray-600">{selectedSurvey.description}</div>}
                                </div>

                                <div className="text-xs text-gray-600 rounded-lg border border-gray-200 bg-gray-50 p-3">
                                    Операторов: {selectedSurvey?.assignment?.operator_ids?.length || 0}
                                    <br />
                                    Стаж: {
                                        selectedSurvey?.assignment?.tenure_weeks_min != null || selectedSurvey?.assignment?.tenure_weeks_max != null
                                            ? `${selectedSurvey?.assignment?.tenure_weeks_min != null ? `от ${selectedSurvey.assignment.tenure_weeks_min} нед.` : 'без минимума'}${selectedSurvey?.assignment?.tenure_weeks_max != null ? ` до ${selectedSurvey.assignment.tenure_weeks_max} нед.` : ''}`
                                            : 'Любой'
                                    }
                                </div>

                                {isOperator && selectedSurvey?.my_assignment?.can_submit && (
                                    <div className="space-y-3">
                                        {(selectedSurvey.questions || []).map((question, index) => {
                                            const answer = answers[question.id] || {};
                                            return (
                                                <div key={question.id} className="border border-gray-200 rounded-lg p-3">
                                                    <div className="text-xs text-gray-500">#{index + 1} | {questionTypeLabel(question.type)}</div>
                                                    <div className="font-medium text-gray-800 mb-2">{question.text}</div>
                                                    {question.type === 'rating' ? (
                                                        <div className="flex gap-2">
                                                            {[1, 2, 3, 4, 5].map((value) => (
                                                                <button
                                                                    key={`${question.id}_${value}`}
                                                                    type="button"
                                                                    onClick={() => updateAnswer(question.id, { rating_value: value })}
                                                                    className={`px-3 py-1 rounded border ${Number(answer.rating_value) === value ? 'bg-amber-500 text-white border-amber-500' : 'border-gray-300'}`}
                                                                >
                                                                    {value}
                                                                </button>
                                                            ))}
                                                        </div>
                                                    ) : (
                                                        <div className="space-y-1">
                                                            {(question.options || []).map((option) => {
                                                                const selected = Array.isArray(answer.selected_options) && answer.selected_options.includes(option);
                                                                return (
                                                                    <label key={`${question.id}_${option}`} className="flex items-center gap-2 text-sm">
                                                                        <input
                                                                            type={question.type === 'single' ? 'radio' : 'checkbox'}
                                                                            name={`q_${question.id}`}
                                                                            checked={selected}
                                                                            onChange={() => {
                                                                                if (question.type === 'single') updateAnswer(question.id, { selected_options: [option] });
                                                                                else {
                                                                                    const set = new Set(answer.selected_options || []);
                                                                                    if (set.has(option)) set.delete(option);
                                                                                    else set.add(option);
                                                                                    updateAnswer(question.id, { selected_options: Array.from(set) });
                                                                                }
                                                                            }}
                                                                        />
                                                                        <span>{option}</span>
                                                                    </label>
                                                                );
                                                            })}
                                                            {question.allow_other && (
                                                                <input
                                                                    value={answer.answer_text || ''}
                                                                    onChange={(event) => updateAnswer(question.id, { answer_text: event.target.value })}
                                                                    placeholder="Другое..."
                                                                    className="w-full p-2 border border-gray-300 rounded-lg text-sm"
                                                                />
                                                            )}
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                        <button onClick={submitSurvey} disabled={isSubmitting} className="px-4 py-2 rounded bg-green-600 text-white disabled:opacity-50">
                                            {isSubmitting ? 'Отправка...' : 'Завершить опрос'}
                                        </button>
                                    </div>
                                )}

                                {(!isOperator || selectedSurvey?.my_assignment?.status === 'completed') && (
                                    <div className="space-y-2">
                                        {(selectedSurvey.questions || []).map((question, index) => (
                                            <div key={question.id} className="border border-gray-200 rounded-lg p-3">
                                                <div className="text-xs text-gray-500">
                                                    #{index + 1} | {questionTypeLabel(question.type)}{question.required ? ' | обязательный' : ''}
                                                </div>
                                                <div className="font-medium text-gray-800">{question.text}</div>
                                            </div>
                                        ))}
                                    </div>
                                )}

                                {canManage && (
                                    <div className="space-y-3">
                                        <div className="text-sm font-medium text-gray-700">Статистика</div>
                                        <div className="text-sm text-gray-700">
                                            Назначено: <strong>{selectedSurvey?.statistics?.assigned_count || 0}</strong>
                                            {' | '}
                                            Пройдено: <strong>{selectedSurvey?.statistics?.completed_count || 0}</strong>
                                            {' | '}
                                            Ответов получено: <strong>{selectedSurvey?.statistics?.responses_count || 0}</strong>
                                            {' | '}
                                            Ожидают: <strong>{selectedSurvey?.statistics?.pending_count || 0}</strong>
                                        </div>
                                        {(selectedSurvey?.statistics?.question_stats || []).map((stat, index) => renderDetailedQuestionStats(stat, index))}
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default SurveysView;
