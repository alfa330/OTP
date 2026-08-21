import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import { normalizeRole, isAdminLikeRole, roleIsAny } from '../../utils/roles';
import {
    APPLE_FONT,
    IosHint,
    IosMenu,
    IosSection,
    IosSegmented,
    IosToggle,
    iosBtnGhost,
    iosCard,
    iosInput
} from '../ui/ios';

const QUESTION_TYPES = [
    { value: 'single', label: 'Один вариант' },
    { value: 'multiple', label: 'Несколько вариантов' },
    { value: 'rating', label: 'Рейтинг 1–5' },
    { value: 'other_only', label: 'Только "Другое"' }
];
const OTHER_ANSWER_MAX_LENGTH = 500;
const QUESTION_TYPE_OTHER_ONLY = 'other_only';

const SCOPE_ACTIVE = 'active';
const SCOPE_ARCHIVE = 'archive';
const LIST_PAGE_SIZE = 20;
const LIST_SEARCH_DEBOUNCE_MS = 300;

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
    <div className="divide-y divide-slate-50">
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
        green: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
        blue: 'bg-blue-50 text-blue-700 ring-1 ring-blue-100',
        amber: 'bg-amber-50 text-amber-700 ring-1 ring-amber-100',
        gray: 'bg-slate-100 text-slate-600',
    };
    return (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${colors[color]}`}>
            {children}
        </span>
    );
};

const ProgressBar = ({ value, color = 'blue' }) => {
    const colors = { blue: 'bg-blue-500', amber: 'bg-amber-400', emerald: 'bg-emerald-500' };
    const pct = Math.max(0, Math.min(100, Number(value) || 0));
    return (
        <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
            <div className={`h-full ${colors[color]} rounded-full transition-all duration-500`} style={{ width: `${pct}%` }} />
        </div>
    );
};

/* ─── Разбор ответа: чистые правила, общие для всех трёх мест показа ───
   Ими пользуются вкладка «Ответы» у руководителя, свой результат у оператора
   и статистика. Раньше это были useCallback внутри компонента — переиспользовать
   их из отдельного блока разбора попытки было нечем. */

const formatQuestionAnswerText = (question, answer) => {
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
};

const getExpectedOptionsForTest = (question, answer) => {
    const fromAnswer = toUniqueTrimmedList(answer?.expected_options);
    if (fromAnswer.length > 0) return fromAnswer;
    return toUniqueTrimmedList(question?.correct_options);
};

const isTestAnswerCorrect = (question, answer) => {
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
};

// Частичный зачёт: сервер присылает и признак, и начисленный балл.
const isTestAnswerPartiallyCorrect = (answer) => (
    answer?.is_partially_correct === true
    || (answer?.is_correct !== true && Number(answer?.earned_points) > 0)
);

const testAnswerStatusMeta = (question, answer, hasAnswer) => {
    if (!hasAnswer) return { label: 'Нет ответа', color: 'gray' };
    if (isTestAnswerCorrect(question, answer)) return { label: 'Верно', color: 'green' };
    if (isTestAnswerPartiallyCorrect(answer)) return { label: 'Частично', color: 'blue' };
    return { label: 'Неверно', color: 'amber' };
};

const hasSurveyAnswer = (question, answer) => {
    if (!question || !answer) return false;
    if (question.type === 'rating') {
        return Number.isFinite(Number(answer?.rating_value));
    }
    const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
    const answerText = String(answer?.answer_text || '').trim();
    return selectedOptions.length > 0 || answerText.length > 0;
};

/* ─── Разбор попытки ───
 *
 * В тесте показываем ВСЕ варианты, как на любом тестовом сайте: видно и что
 * человек выбрал, и что было правильным, и — главное — чего он НЕ выбрал,
 * хотя следовало. Строкой «правильный ответ: Город, Номер телефона» это не
 * читается: глазами приходится сопоставлять два списка.
 *
 * Правильные варианты рисуем только если они известны: оператору внутри
 * открытого окна теста сервер их не отдаёт, и «пустой» разбор не должен
 * превращаться в подсказку.
 */
const ReviewOptionRow = ({ option, isMultiple, isChosen, isCorrect, revealCorrect }) => {
    // Красный — только там, где человек ОШИБСЯ: выбрал вариант, который
    // оказался неверным. Прочие неверные варианты красить не надо — их никто
    // не выбирал, и три красные строки из четырёх читались бы как «всё плохо»
    // вместо «вот здесь ошибка». Зелёный остаётся у правильных: и у того,
    // что человек угадал, и у того, что пропустил.
    const rightChoice = revealCorrect && isChosen && isCorrect;
    const wrongChoice = revealCorrect && isChosen && !isCorrect;
    const missedRight = revealCorrect && !isChosen && isCorrect;

    const tone = rightChoice
        ? 'bg-emerald-50 ring-emerald-200'
        : wrongChoice
            ? 'bg-rose-50 ring-rose-300'
            : missedRight
                ? 'bg-emerald-50/50 ring-emerald-200'
                : (isChosen ? 'bg-slate-100 ring-slate-200' : 'bg-white ring-slate-200/70');

    const markTone = rightChoice
        ? 'border-emerald-500 bg-emerald-500 text-white'
        : wrongChoice
            ? 'border-rose-500 bg-rose-500 text-white'
            : missedRight
                ? 'border-emerald-400 text-emerald-600'
                : (isChosen ? 'border-slate-400 bg-slate-400 text-white' : 'border-slate-300 text-transparent');

    const textTone = rightChoice || missedRight
        ? 'text-emerald-900'
        : (wrongChoice ? 'text-rose-900' : 'text-slate-700');

    return (
        <div className={`flex items-center gap-2.5 rounded-xl px-3 py-2 ring-1 transition-colors ${tone}`}>
            <span
                className={`grid h-[18px] w-[18px] shrink-0 place-items-center border-2 text-[9px] ${
                    isMultiple ? 'rounded-[5px]' : 'rounded-full'
                } ${markTone}`}
            >
                <FaIcon className={`fas ${wrongChoice ? 'fa-times' : 'fa-check'}`} />
            </span>
            <span className={`min-w-0 flex-1 break-words text-[13px] ${textTone}`}>
                {option}
            </span>
            <span className="shrink-0 text-[11px] font-medium">
                {rightChoice && <span className="text-emerald-700">Верно · выбрано</span>}
                {wrongChoice && <span className="text-rose-600">Неверно · выбрано</span>}
                {missedRight && <span className="text-emerald-700">Правильный</span>}
                {!revealCorrect && isChosen && <span className="text-slate-500">Выбрано</span>}
            </span>
        </div>
    );
};

const AttemptReview = ({ questions = [], getAnswer, isTest = false, selfView = false }) => {
    if (!questions.length) {
        return <div className="py-6 text-center text-[13px] text-slate-400">В этом опросе нет вопросов</div>;
    }
    return (
        <div className="space-y-2.5">
            {questions.map((question, index) => {
                const answer = getAnswer(question, index);
                const resolvedQuestion = answer?.__question || question;
                const hasAnswer = hasSurveyAnswer(resolvedQuestion, answer);
                const expectedOptions = isTest ? getExpectedOptionsForTest(resolvedQuestion, answer) : [];
                const revealCorrect = isTest && expectedOptions.length > 0;
                const status = isTest ? testAnswerStatusMeta(resolvedQuestion, answer, hasAnswer) : null;
                const earnedPoints = Number(answer?.earned_points);
                const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
                const otherText = String(answer?.answer_text || '').trim();
                const options = toUniqueTrimmedList(resolvedQuestion?.options);
                const type = String(resolvedQuestion?.type || '');

                return (
                    <div key={`review_${index}_${resolvedQuestion?.id || 'q'}`} className="rounded-2xl bg-white p-3.5 ring-1 ring-slate-200/70">
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">
                                    Вопрос {index + 1}
                                </div>
                                <div className="mt-0.5 text-[13.5px] font-medium text-slate-900">
                                    {resolvedQuestion?.text || `Вопрос ${index + 1}`}
                                </div>
                            </div>
                            {status && (
                                <div className="flex shrink-0 items-center gap-2">
                                    <Badge color={status.color}>{status.label}</Badge>
                                    {Number.isFinite(earnedPoints) && (
                                        <span className="text-[11px] tabular-nums text-slate-400">
                                            {formatPoints(earnedPoints)} / {formatPoints(resolvedQuestion?.points)}
                                        </span>
                                    )}
                                </div>
                            )}
                        </div>

                        <div className="mt-2.5 space-y-1.5">
                            {/* Тест — всегда полный список вариантов. Обычный опрос
                                этого не требует: там нет «правильного», и пять строк
                                вместо одного ответа были бы шумом. */}
                            {isTest && type !== 'rating' && options.length > 0 && options.map((option) => (
                                <ReviewOptionRow
                                    key={`review_${index}_opt_${option}`}
                                    option={option}
                                    isMultiple={type === 'multiple'}
                                    isChosen={selectedOptions.includes(option)}
                                    isCorrect={expectedOptions.includes(option)}
                                    revealCorrect={revealCorrect}
                                />
                            ))}

                            {(!isTest || type === 'rating' || options.length === 0) && (
                                <div className={`rounded-xl px-3 py-2.5 text-[13px] ${
                                    !hasAnswer ? 'bg-slate-50 text-slate-400' : 'bg-slate-50 text-slate-800'
                                }`}>
                                    {hasAnswer
                                        ? (type === 'rating'
                                            ? `${formatQuestionAnswerText(resolvedQuestion, answer)} из 5`
                                            : formatQuestionAnswerText(resolvedQuestion, answer))
                                        : 'Нет ответа'}
                                </div>
                            )}

                            {isTest && type !== 'rating' && options.length > 0 && !hasAnswer && (
                                <div className="rounded-xl bg-slate-50 px-3 py-2 text-[12.5px] text-slate-400">
                                    {selfView ? 'Вы не ответили на этот вопрос' : 'Сотрудник не ответил на этот вопрос'}
                                </div>
                            )}

                            {isTest && otherText && (
                                <div className="rounded-xl bg-slate-50 px-3 py-2 text-[12.5px] text-slate-600">
                                    Свой вариант: {otherText}
                                </div>
                            )}
                        </div>
                    </div>
                );
            })}
        </div>
    );
};

/* ─── main component ─── */

const SurveysView = ({ user, operators = [], directions = [], departments = [], showToast, apiBaseUrl, onSurveyProgressChanged }) => {
    // Список — лёгкие строки страницы, карточка — отдельный запрос.
    // Раньше раздел выкачивал все опросы со всеми ответами разом; теперь
    // с сервера приезжает ровно то, что видно на экране.
    const [surveyRows, setSurveyRows] = useState([]);
    const [listTotal, setListTotal] = useState(0);
    const [listScope, setListScope] = useState(SCOPE_ACTIVE);
    const [listPage, setListPage] = useState(1);
    const [listQueryInput, setListQueryInput] = useState('');
    const [listQuery, setListQuery] = useState('');
    const [assignableGroups, setAssignableGroups] = useState([]);
    const [selectedSurveyId, setSelectedSurveyId] = useState('');
    const [selectedSurvey, setSelectedSurvey] = useState(null);
    const [selectedRepetitions, setSelectedRepetitions] = useState([]);
    const [isDetailLoading, setIsDetailLoading] = useState(false);
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
    const [activeTab, setActiveTab] = useState('questions'); // 'questions' | 'answers' | 'stats'
    const [statsOperatorQuery, setStatsOperatorQuery] = useState('');
    const [openedRespondentKey, setOpenedRespondentKey] = useState(null);
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
        // Из открытой карточки добираем тех, кого нет в общем справочнике
        // (например, уже уволенных): иначе в повторе и в статистике вместо
        // фамилии остался бы «#123».
        (selectedSurvey?.assignment?.operators || []).forEach((assignmentOperator) => {
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
        return Array.from(map.values());
    }, [operators, selectedSurvey?.assignment?.operators]);

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

    // Строка поиска не должна дёргать сервер на каждой букве.
    useEffect(() => {
        const timerId = window.setTimeout(() => setListQuery(listQueryInput.trim()), LIST_SEARCH_DEBOUNCE_MS);
        return () => window.clearTimeout(timerId);
    }, [listQueryInput]);

    // Смена вкладки, поиска или отдела возвращает на первую страницу:
    // иначе после сужения списка человек оказывался бы на пустой странице.
    useEffect(() => { setListPage(1); }, [listScope, listQuery, departmentFilter]);

    const loadSurveyList = useCallback(async () => {
        if (!apiBaseUrl || !user?.id) return;
        setIsLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/surveys`, {
                headers,
                params: {
                    scope: listScope,
                    page: listPage,
                    page_size: LIST_PAGE_SIZE,
                    ...(listQuery ? { q: listQuery } : {}),
                    ...(departmentFilter ? { department_id: departmentFilter } : {})
                }
            });
            setSurveyRows(Array.isArray(response?.data?.surveys) ? response.data.surveys : []);
            setListTotal(Number(response?.data?.total || 0));
            // Состав групп приходит тем же ответом — отдельный запрос не нужен.
            if (Array.isArray(response?.data?.groups)) setAssignableGroups(response.data.groups);
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось загрузить опросы', 'error');
            setSurveyRows([]);
            setListTotal(0);
        } finally {
            setIsLoading(false);
        }
    }, [apiBaseUrl, departmentFilter, headers, listPage, listQuery, listScope, notify, user?.id]);

    useEffect(() => { loadSurveyList(); }, [loadSurveyList]);

    const loadSurveyDetail = useCallback(async (surveyId) => {
        if (!apiBaseUrl || !user?.id || !surveyId) {
            setSelectedSurvey(null);
            setSelectedRepetitions([]);
            return;
        }
        setIsDetailLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/surveys/${surveyId}/detail`, { headers });
            setSelectedSurvey(response?.data?.survey || null);
            setSelectedRepetitions(Array.isArray(response?.data?.repetitions) ? response.data.repetitions : []);
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось открыть опрос', 'error');
            setSelectedSurvey(null);
            setSelectedRepetitions([]);
        } finally {
            setIsDetailLoading(false);
        }
    }, [apiBaseUrl, headers, notify, user?.id]);

    // Перечитать и список, и открытую карточку — после создания, правки,
    // прохождения и удаления. Счётчики строки и содержимое карточки живут
    // в разных запросах, и обновлять их надо парой.
    const reloadSurveys = useCallback(async (surveyId) => {
        const targetId = surveyId === undefined ? selectedSurveyId : surveyId;
        await Promise.all([
            loadSurveyList(),
            targetId ? loadSurveyDetail(targetId) : Promise.resolve()
        ]);
        // Бейдж раздела — отдельный дешёвый счётчик; трогаем его при входе
        // и после изменений, а не на каждую страницу и каждую букву поиска.
        if (typeof onSurveyProgressChangedRef.current === 'function') {
            onSurveyProgressChangedRef.current();
        }
    }, [loadSurveyDetail, loadSurveyList, selectedSurveyId]);

    useEffect(() => {
        if (typeof onSurveyProgressChangedRef.current === 'function') {
            onSurveyProgressChangedRef.current();
        }
    }, []);

    // Выбор по умолчанию — первая строка страницы; исчезнувший из выборки
    // опрос уступает место первому доступному.
    useEffect(() => {
        if (isLoading) return;
        if (selectedSurveyId && surveyRows.some((item) => String(item.id) === String(selectedSurveyId))) return;
        setSelectedSurveyId(surveyRows[0]?.id || '');
    }, [isLoading, selectedSurveyId, surveyRows]);

    useEffect(() => { loadSurveyDetail(selectedSurveyId); }, [loadSurveyDetail, selectedSurveyId]);

    useEffect(() => {
        setStatsOperatorQuery('');
        setOpenedRespondentKey(null);
        setActiveTab('questions');
    }, [selectedSurveyId]);

    const listPages = useMemo(
        () => Math.max(1, Math.ceil((Number(listTotal) || 0) / LIST_PAGE_SIZE)),
        [listTotal]
    );

    const selectedListRow = useMemo(
        () => surveyRows.find((item) => String(item.id) === String(selectedSurveyId)) || null,
        [selectedSurveyId, surveyRows]
    );

    const assignmentMatchesSelectedDepartment = useCallback((assignment) => {
        if (selectedDepartmentId == null) return true;
        const directDepartmentId = Number(assignment?.department_id ?? assignment?.departmentId);
        if (Number.isFinite(directDepartmentId)) return directDepartmentId === selectedDepartmentId;
        const operatorId = Number(assignment?.operator_id ?? assignment?.id);
        const mappedDepartmentId = operatorDepartmentIdById.get(operatorId);
        return Number(mappedDepartmentId) === selectedDepartmentId;
    }, [operatorDepartmentIdById, selectedDepartmentId]);

    // Счётчики строки списка считает сервер — уже с учётом отдела и прав.
    // Пересчитывать их на клиенте нечем и незачем: назначений в лёгкой
    // строке нет вовсе.
    const getSurveyDisplayMetrics = useCallback((survey) => {
        const statistics = survey?.statistics || {};
        const assignedCount = Number(statistics.assigned_count || 0);
        const completedCount = Number(statistics.completed_count || 0);
        const pendingCount = Number(statistics.pending_count ?? Math.max(0, assignedCount - completedCount));
        const completionRate = Number(statistics.completion_rate || 0);
        return {
            assignedCount,
            completedCount,
            pendingCount,
            completionRate: Number.isFinite(completionRate) ? completionRate : 0
        };
    }, []);

    // В открытой карточке назначения есть, поэтому срез по отделу считается
    // здесь — списку он уже приехал готовым.
    const selectedSurveyDisplayMetrics = useMemo(() => {
        const assignments = Array.isArray(selectedSurvey?.assignment?.operators)
            ? selectedSurvey.assignment.operators
            : [];
        if (selectedDepartmentId == null || assignments.length === 0) {
            return getSurveyDisplayMetrics(selectedSurvey || selectedListRow);
        }
        const departmentAssignments = assignments.filter(assignmentMatchesSelectedDepartment);
        const assignedCount = departmentAssignments.length;
        const completedCount = departmentAssignments.filter(
            (assignment) => String(assignment?.status || '').trim().toLowerCase() === 'completed'
        ).length;
        return {
            assignedCount,
            completedCount,
            pendingCount: Math.max(0, assignedCount - completedCount),
            completionRate: assignedCount > 0 ? Math.round((completedCount / assignedCount) * 1000) / 10 : 0
        };
    }, [
        assignmentMatchesSelectedDepartment,
        getSurveyDisplayMetrics,
        selectedDepartmentId,
        selectedListRow,
        selectedSurvey
    ]);

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

    // Вопросы соседних прогонов повтора: ответ из прошлого запуска надо
    // показывать с ТЕМ вопросом, на который отвечали, а не с нынешним.
    const surveyQuestionsBySurveyId = useMemo(() => {
        const map = new Map();
        const sortQuestions = (questions) => (Array.isArray(questions) ? [...questions] : []).sort((a, b) => {
            const posA = Number(a?.position) || 0;
            const posB = Number(b?.position) || 0;
            if (posA !== posB) return posA - posB;
            return (Number(a?.id) || 0) - (Number(b?.id) || 0);
        });
        const selectedId = Number(selectedSurvey?.id);
        if (Number.isFinite(selectedId)) map.set(selectedId, sortQuestions(selectedSurvey?.questions));
        (selectedRepetitions || []).forEach((repetition) => {
            const repetitionId = Number(repetition?.id);
            if (!Number.isFinite(repetitionId)) return;
            map.set(repetitionId, sortQuestions(repetition?.questions));
        });
        return map;
    }, [selectedRepetitions, selectedSurvey?.id, selectedSurvey?.questions]);

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


    /* ─── Вкладка «Ответы»: карточки сотрудников вместо широкой таблицы ─── */

    // Таблица с колонкой на каждый вопрос читалась только вбок и только при
    // трёх вопросах. Здесь — карточка на человека, а его ответы открываются
    // отдельным листом: вопрос, ответ, а у теста ещё и правильный вариант.
    const respondentsTotalCount = departmentFilteredDetailedStatsRows.length;

    // Карточки строим по ВСЕМ сотрудникам среза, а поиск фильтрует уже готовые:
    // иначе цифры в шапке вкладки («прошли», «средний результат») прыгали бы
    // от набора в строке поиска и перестали бы что-либо значить.
    const respondentCardsAll = useMemo(() => {
        const cards = departmentFilteredDetailedStatsRows.map((row) => {
            const repeatSurveyId = Number(row?.repeat_survey_id || selectedSurvey?.id || 0);
            const operatorId = Number(row?.operator_id || 0);
            const testSummary = row?.test_summary || {};
            const scoreRaw = testSummary?.score_percent;
            const hasScore = (
                scoreRaw !== null
                && scoreRaw !== undefined
                && `${scoreRaw}`.trim() !== ''
                && Number.isFinite(Number(scoreRaw))
            );
            const rowQuestions = surveyQuestionsBySurveyId.get(repeatSurveyId)
                || (selectedSurvey?.questions || []);
            const answeredCount = rowQuestions.reduce((count, question, questionIndex) => {
                const resolved = resolveStatsQuestionAndAnswer(row, question, questionIndex);
                return count + (hasSurveyAnswer(resolved.question, resolved.answer) ? 1 : 0);
            }, 0);

            return {
                key: `${operatorId}_${repeatSurveyId}`,
                row,
                operatorId,
                repeatSurveyId,
                questions: rowQuestions,
                name: row?.operator_name || `#${row?.operator_id || '—'}`,
                isDismissed: !!row?.is_operator_dismissed,
                isCompleted: String(row?.status || '').toLowerCase() === 'completed',
                submittedAt: row?.submitted_at,
                repeatIteration: Number(
                    row?.repeat_iteration != null ? row.repeat_iteration : (selectedSurvey?.repeat?.iteration || 1)
                ),
                answeredCount,
                questionsCount: rowQuestions.length,
                testSummary,
                hasScore,
                scoreValue: hasScore ? Number(scoreRaw) : null
            };
        });

        // Прошедшие — сверху: вкладка о них. Внутри группы тест сортируем по
        // результату, обычный опрос — по времени отправки.
        return cards.sort((a, b) => {
            if (a.isCompleted !== b.isCompleted) return a.isCompleted ? -1 : 1;
            if (isTestStatsSurvey && a.hasScore && b.hasScore && a.scoreValue !== b.scoreValue) {
                return b.scoreValue - a.scoreValue;
            }
            return String(a.name).localeCompare(String(b.name), 'ru', { sensitivity: 'base' });
        });
    }, [
        departmentFilteredDetailedStatsRows,
        hasSurveyAnswer,
        isTestStatsSurvey,
        resolveStatsQuestionAndAnswer,
        selectedSurvey?.id,
        selectedSurvey?.questions,
        selectedSurvey?.repeat?.iteration,
        surveyQuestionsBySurveyId
    ]);

    const respondentCards = useMemo(() => {
        const query = String(statsOperatorQuery || '').trim().toLowerCase();
        if (!query) return respondentCardsAll;
        return respondentCardsAll.filter((card) => (
            card.name.toLowerCase().includes(query) || String(card.operatorId).includes(query)
        ));
    }, [respondentCardsAll, statsOperatorQuery]);

    // Средний балл теста — по тем, кто его прошёл. Медиана здесь была бы
    // честнее на выбросах, но в тесте важен именно средний процент: он же
    // уходит в качество оператора.
    const openedRespondent = useMemo(
        () => respondentCardsAll.find((card) => card.key === openedRespondentKey) || null,
        [openedRespondentKey, respondentCardsAll]
    );

    // Escape возвращает к списку карточек — так же, как кнопка «Назад».
    // Обработчик снимаем вместе с закрытием: висящий слушатель перехватывал бы
    // Escape у конструктора.
    useEffect(() => {
        if (!openedRespondentKey) return undefined;
        const onKeyDown = (event) => {
            if (event.key === 'Escape') setOpenedRespondentKey(null);
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [openedRespondentKey]);

    const respondentsSummary = useMemo(() => {
        const completed = respondentCardsAll.filter((card) => card.isCompleted);
        const scored = completed.filter((card) => card.hasScore);
        const averageScore = scored.length
            ? scored.reduce((sum, card) => sum + card.scoreValue, 0) / scored.length
            : null;
        return {
            completedCount: completed.length,
            pendingCount: Math.max(0, respondentCardsAll.length - completed.length),
            averageScore
        };
    }, [respondentCardsAll]);

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

    // Прогресс прохождения считаем по тем же правилам, по которым сервер
    // считает вопрос отвеченным, — иначе полоса дошла бы до конца, а отправка
    // упёрлась бы в «ответьте на все обязательные вопросы».
    const isFillAnswerFilled = useCallback((question, answer) => {
        if (!question) return false;
        if (question.type === 'rating') return Number.isFinite(Number(answer?.rating_value));
        const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
        const otherText = String(answer?.answer_text || '').trim();
        return selectedOptions.length > 0 || otherText.length > 0;
    }, []);

    const fillProgress = useMemo(() => {
        const questions = selectedSurvey?.questions || [];
        const answered = questions.reduce(
            (count, question) => count + (isFillAnswerFilled(question, answers[question.id]) ? 1 : 0),
            0
        );
        return {
            answered,
            total: questions.length,
            percent: questions.length ? (answered / questions.length) * 100 : 0
        };
    }, [answers, isFillAnswerFilled, selectedSurvey?.questions]);

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

    // Escape закрывает конструктор. Прокрутку страницы больше НЕ блокируем:
    // конструктор — обычная панель, а не оверлей, и заблокированный body
    // просто не давал бы долистать до его нижней части.
    useEffect(() => {
        if (!showBuilder) return undefined;
        const onKeyDown = (event) => {
            if (event.key === 'Escape' && !isSaving) closeBuilder();
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [showBuilder, isSaving, closeBuilder]);

    // Панель открывается на месте списка — если страница была прокручена,
    // человек увидел бы её середину и решил, что ничего не произошло.
    useEffect(() => {
        if (showBuilder) window.scrollTo({ top: 0, behavior: 'smooth' });
    }, [showBuilder]);

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

    // Строка списка лёгкая — в ней нет ни вопросов, ни назначений. Поэтому
    // «Редактировать» и «Повторить» из меню строки сначала берут карточку,
    // а уже потом открывают конструктор.
    const openSurveyInBuilder = useCallback(async (surveyId, mode) => {
        if (!canManage || !surveyId) return;
        let survey = (selectedSurvey && String(selectedSurvey.id) === String(surveyId))
            ? selectedSurvey
            : null;
        if (!survey) {
            try {
                const response = await axios.get(`${apiBaseUrl}/api/surveys/${surveyId}/detail`, { headers });
                survey = response?.data?.survey || null;
            } catch (error) {
                notify(error?.response?.data?.error || 'Не удалось открыть опрос', 'error');
                return;
            }
        }
        if (!survey) return;
        if (mode === 'repeat') startRepeatSurvey(survey);
        else startEditSurvey(survey);
    }, [apiBaseUrl, canManage, headers, notify, selectedSurvey, startEditSurvey, startRepeatSurvey]);

    const startEditSurveyById = useCallback((surveyId) => openSurveyInBuilder(surveyId, 'edit'), [openSurveyInBuilder]);
    const startRepeatSurveyById = useCallback((surveyId) => openSurveyInBuilder(surveyId, 'repeat'), [openSurveyInBuilder]);


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
            if (!isEditMode) {
                // Новый опрос лежит в активных и сверху — иначе после создания
                // человек оставался бы на третьей странице архива, с чужим
                // поиском в строке, и решал, что ничего не сохранилось.
                setListScope(SCOPE_ACTIVE);
                setListPage(1);
                setListQueryInput('');
            }
            await reloadSurveys();
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
            if (String(surveyId) === String(selectedSurveyId)) {
                // Открытую карточку гасим сразу: перечитывать удалённый опрос
                // незачем, а выбор новой строки сделает эффект списка.
                setSelectedSurveyId('');
                setSelectedSurvey(null);
                setSelectedRepetitions([]);
            }
            await loadSurveyList();
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
            await reloadSurveys();
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось отправить ответы', 'error');
            // Время могло истечь ровно во время отправки — тогда результат
            // закроет автоотправка, а список надо обновить.
            if (error?.response?.status === 409) await reloadSurveys();
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

    /* ─── Статистика по вопросу ───
     *
     * Одна карточка на вопрос, внутри — распределение ответов. Цветом
     * выделен только правильный вариант теста: он единственный здесь несёт
     * смысл, остальные полосы нейтральные, иначе рябит.
     *
     * Доля считается от числа ОТВЕТИВШИХ на этот вопрос, а не от всех
     * назначенных: иначе у вопроса, который многие пропустили, все варианты
     * выглядели бы одинаково провальными.
     */
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
        const leaderCount = options.reduce((max, option) => Math.max(max, Number(option?.count || 0)), 0);

        return (
            <div key={`${selectedSurvey?.id || 'survey'}_stat_${index}`} className="rounded-2xl bg-white p-4 ring-1 ring-slate-200/70">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <div className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">
                            Вопрос {index + 1}
                        </div>
                        <div className="mt-0.5 text-[13.5px] font-medium text-slate-900">{questionText}</div>
                    </div>
                    <div className="shrink-0 text-right">
                        <div className="text-[15px] font-semibold tabular-nums text-slate-900">{answeredCount}</div>
                        <div className="text-[10.5px] text-slate-400">ответили</div>
                    </div>
                </div>

                {skippedCount > 0 && (
                    <div className="mt-1.5 text-[11.5px] text-slate-400">
                        Пропустили: <span className="tabular-nums">{skippedCount}</span>
                    </div>
                )}

                {stat.type === 'rating' && (
                    <div className="mt-3 space-y-2.5">
                        <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[12px] text-slate-500">
                            <span className="text-[20px] font-semibold tabular-nums leading-none text-slate-900">
                                {stat.average_rating ?? '—'}
                            </span>
                            <span>средняя из 5</span>
                            <span>медиана <strong className="tabular-nums text-slate-700">{stat.median_rating ?? '—'}</strong></span>
                            <span>разброс <strong className="tabular-nums text-slate-700">{stat.min_rating ?? '—'}–{stat.max_rating ?? '—'}</strong></span>
                        </div>
                        <div className="space-y-1.5">
                            {ratingDistribution.map((bucket) => {
                                const value = Number(bucket.value);
                                const count = Number(bucket.count || 0);
                                const percentAnswers = Number(bucket.percent_of_answers || 0);
                                return (
                                    <div key={`${selectedSurvey?.id || 'survey'}_stat_${index}_rating_${value}`} className="flex items-center gap-3">
                                        <span className="w-6 shrink-0 text-[11.5px] tabular-nums text-slate-500">{value}</span>
                                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                                            <div
                                                className="h-full rounded-full bg-slate-400 transition-all duration-500"
                                                style={{ width: percentToWidth(percentAnswers) }}
                                            />
                                        </div>
                                        <span className="w-24 shrink-0 text-right text-[11.5px] tabular-nums text-slate-500">
                                            {count} · {formatPercent(percentAnswers)}
                                        </span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {stat.type !== 'rating' && (
                    <div className="mt-3 space-y-1.5">
                        {options.length === 0 && (
                            <div className="text-[12px] text-slate-400">Ответов на этот вопрос пока нет.</div>
                        )}
                        {options.map((option, optionIndex) => {
                            const optionLabel = String(option?.option || `Вариант ${optionIndex + 1}`);
                            const optionCount = Number(option?.count || 0);
                            const percentAnswers = Number(option?.percent_of_answers != null ? option.percent_of_answers : option?.percent || 0);
                            const isCorrectOption = isTestStatsSurvey && expectedOptionsSet.has(optionLabel);
                            const isLeader = leaderCount > 0 && optionCount === leaderCount;
                            return (
                                <div
                                    key={`${selectedSurvey?.id || 'survey'}_stat_${index}_option_${optionIndex}`}
                                    className={`rounded-xl px-3 py-2 ring-1 ${
                                        isCorrectOption ? 'bg-emerald-50/60 ring-emerald-200' : 'bg-white ring-slate-200/70'
                                    }`}
                                >
                                    <div className="flex items-center justify-between gap-2 text-[12.5px]">
                                        <span className={`min-w-0 flex-1 truncate ${isCorrectOption ? 'font-medium text-emerald-900' : 'text-slate-700'}`} title={optionLabel}>
                                            {optionLabel}
                                            {isCorrectOption && (
                                                <FaIcon className="fas fa-check ml-1.5 text-[10px] text-emerald-600" />
                                            )}
                                        </span>
                                        <span className="shrink-0 tabular-nums text-slate-500">
                                            {optionCount} · {formatPercent(percentAnswers)}
                                        </span>
                                    </div>
                                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
                                        <div
                                            className={`h-full rounded-full transition-all duration-500 ${
                                                isCorrectOption ? 'bg-emerald-500' : (isLeader ? 'bg-slate-500' : 'bg-slate-300')
                                            }`}
                                            style={{ width: percentToWidth(percentAnswers) }}
                                        />
                                    </div>
                                </div>
                            );
                        })}
                        {stat.type === 'multiple' && Number(stat.selections_total || 0) > 0 && (
                            <div className="flex items-center gap-1.5 pt-0.5 text-[11px] text-slate-400">
                                Всего выборов: <strong className="tabular-nums text-slate-600">{Number(stat.selections_total || 0)}</strong>
                                <IosHint
                                    label="Про сумму выборов"
                                    text="В вопросе с несколькими ответами один человек мог отметить сразу несколько вариантов, поэтому сумма выборов больше числа ответивших, а доли в сумме дают больше 100%."
                                />
                            </div>
                        )}
                    </div>
                )}
            </div>
        );
    };

    /* ─── render ─── */
    return (
        // Шрифт задаём на корне раздела, а не только в конструкторе: половина
        // экрана в SF Pro, половина в системном — это и читается как «разные
        // куски сайта».
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>

            {/* ── Page header ── */}
            <div className={`${iosCard} overflow-hidden`}>
                <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
                    <div className="flex items-center gap-3">
                        <div className="grid h-10 w-10 place-items-center rounded-xl bg-blue-600 shadow-sm">
                            <FaIcon className="fas fa-list-alt text-base text-white" />
                        </div>
                        <div className="flex items-center gap-2">
                            <h2 className="text-[19px] font-bold leading-tight text-slate-900">Опросы</h2>
                            {/* Пояснение к разделу нужно один раз, а строку под
                                заголовком занимало всегда — прячем под «i». */}
                            <IosHint
                                label="О разделе"
                                text={canManage
                                    ? 'Создание и назначение опросов и тестов по стажу, направлению, группам и конкретным сотрудникам. Опросы старше двух недель уходят в архив: они перестают показываться сотрудникам и не считаются в уведомлениях раздела.'
                                    : 'Здесь показаны назначенные вам опросы и тесты. Опросы старше двух недель уходят в архив — пройти их уже нельзя, но свои ответы можно посмотреть на вкладке «Архив».'}
                            />
                        </div>
                    </div>
                    {canManage && (
                        <div className="flex flex-wrap items-center justify-end gap-2">
                            {canFilterByDepartment && (
                                <div className="flex items-center gap-2">
                                    <FaIcon className="fa-solid fa-layer-group text-slate-400" />
                                    <select
                                        value={departmentFilter}
                                        onChange={(event) => setDepartmentFilter(event.target.value)}
                                        className="rounded-xl bg-slate-100 px-3 py-2 text-[13px] text-slate-800 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
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
                                className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-[13.5px] font-semibold shadow-sm transition-all active:scale-[0.98] ${
                                    showBuilder
                                        ? 'bg-slate-100 text-slate-600 hover:bg-slate-200'
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

            {/* ── Конструктор опроса ──
                Не модалка: конструктор — это отдельный режим работы, а не
                короткое подтверждение. Затемнённый фон и оверлей поверх списка
                мешали сверяться с уже созданными опросами, и на телефоне лист
                на весь экран всё равно вырождался в страницу. Поэтому обычная
                панель на месте списка: пока она открыта, список не нужен. */}
            {canManage && showBuilder && (
                <div className="flex animate-[fadeIn_.22s_ease-out] flex-col overflow-hidden rounded-2xl bg-slate-50 ring-1 ring-slate-200/70">
                    <div className="flex flex-col">
                        {/* Header */}
                        <div className="relative flex items-center justify-between gap-3 border-b border-slate-200/70 bg-white px-5 py-3.5">
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
                        <div className="space-y-5 px-4 py-5 sm:px-5">

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
                        <div className="sticky bottom-0 flex items-center justify-between gap-3 border-t border-slate-200/70 bg-white/90 px-5 py-3 backdrop-blur-xl">
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

            {/* ── Список и карточка. Пока открыт конструктор, они скрыты:
                две колонки под панелью редактирования — это две очереди
                внимания на одном экране. ── */}
            {!(canManage && showBuilder) && (
            <>
            {/* ── Main content: list + detail ──
                На широком экране обе колонки держат высоту окна и прокручиваются
                внутри себя: страница списка на 20 строк иначе уводила бы кнопки
                «Назад / Вперёд» далеко вниз, за пределы экрана. На узком —
                колонки складываются в столбик и растут как обычно. */}
            <div className="grid grid-cols-1 gap-5 xl:h-[calc(100vh-13rem)] xl:min-h-[520px] xl:grid-cols-5">

                {/* Survey list */}
                <div className={`xl:col-span-2 ${iosCard} flex flex-col overflow-hidden`}>
                    <div className="space-y-3 border-b border-slate-200/70 px-4 pb-3 pt-3.5">
                        <div className="flex items-center justify-between gap-2">
                            <span className="text-[13.5px] font-semibold text-slate-900">Список опросов</span>
                            <span className="text-[12px] tabular-nums text-slate-400">{listTotal}</span>
                        </div>
                        {/* Активные и архив — разные списки, а не фильтр внутри одного:
                            архив копится и без разделения затопил бы рабочую очередь. */}
                        <IosSegmented
                            value={listScope}
                            onChange={setListScope}
                            stretch
                            ariaLabel="Активные или архив"
                            options={[
                                { value: SCOPE_ACTIVE, label: 'Активные' },
                                { value: SCOPE_ARCHIVE, label: 'Архив' }
                            ]}
                        />
                        <div className="relative">
                            <FaIcon className="fas fa-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-400" />
                            <input
                                value={listQueryInput}
                                onChange={(event) => setListQueryInput(event.target.value)}
                                placeholder="Поиск по названию"
                                className={`${iosInput} py-2 pl-8 text-[13px]`}
                            />
                        </div>
                    </div>

                    <div className="thin-scroll flex-1 divide-y divide-slate-100 overflow-y-auto">
                        {isLoading && <SurveysListSkeleton />}
                        {!isLoading && surveyRows.length === 0 && (
                            <div className="p-8 text-center">
                                <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-slate-50">
                                    <FaIcon className={`fas ${listScope === SCOPE_ARCHIVE ? 'fa-box-archive' : 'fa-clipboard-list'} text-xl text-slate-300`} />
                                </div>
                                <p className="text-[13px] text-slate-400">
                                    {listQuery
                                        ? 'Ничего не нашлось'
                                        : (listScope === SCOPE_ARCHIVE
                                            ? 'В архиве пока пусто'
                                            : (isOperator ? 'Назначенных опросов пока нет' : 'Опросов пока нет'))}
                                </p>
                            </div>
                        )}
                        {!isLoading && surveyRows.map((survey) => {
                            const isSelected = String(survey.id) === String(selectedSurveyId);
                            const isCompleted = survey?.my_assignment?.status === 'completed';
                            const displayMetrics = getSurveyDisplayMetrics(survey);
                            const completionRate = displayMetrics.completionRate || 0;
                            const repeatIteration = Number(survey?.repeat?.iteration || 1);
                            const listTestStatus = (survey?.is_test && !survey?.is_archived)
                                ? testStatusMeta(survey?.test?.status)
                                : null;
                            return (
                                <div
                                    key={survey.id}
                                    role="button"
                                    tabIndex={0}
                                    className={`group relative cursor-pointer px-4 py-3 transition-colors ${isSelected ? 'bg-blue-50/70' : 'hover:bg-slate-50'}`}
                                    onClick={() => setSelectedSurveyId(survey.id)}
                                    onKeyDown={(event) => {
                                        if (event.key === 'Enter' || event.key === ' ') {
                                            event.preventDefault();
                                            setSelectedSurveyId(survey.id);
                                        }
                                    }}
                                >
                                    {isSelected && <div className="absolute inset-y-0 left-0 w-0.5 rounded-r-full bg-blue-500" />}
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="min-w-0 flex-1">
                                            <div className="flex min-w-0 items-center gap-1.5">
                                                <div className={`truncate text-[13.5px] font-semibold ${isSelected ? 'text-blue-700' : 'text-slate-800'}`}>
                                                    {survey.title}
                                                </div>
                                                {survey?.is_test && (
                                                    <span className="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                                                        Тест
                                                    </span>
                                                )}
                                                {repeatIteration > 1 && (
                                                    <span className="shrink-0 rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">
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
                                                        <div className="flex items-center justify-between text-[11px] text-slate-500">
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
                                            /* Действия за «тремя точками»: строка списка не должна
                                               нести кнопку «Удалить» рядом с каждым названием. */
                                            <div onClick={(event) => event.stopPropagation()}>
                                                <IosMenu
                                                    align="right"
                                                    items={[
                                                        {
                                                            key: 'edit',
                                                            label: 'Редактировать',
                                                            icon: (props) => <FaIcon {...props} className="fas fa-edit" />,
                                                            onSelect: () => { setSelectedSurveyId(survey.id); startEditSurveyById(survey.id); }
                                                        },
                                                        {
                                                            key: 'repeat',
                                                            label: 'Повторить',
                                                            icon: (props) => <FaIcon {...props} className="fas fa-redo" />,
                                                            onSelect: () => { setSelectedSurveyId(survey.id); startRepeatSurveyById(survey.id); }
                                                        },
                                                        {
                                                            key: 'delete',
                                                            label: 'Удалить',
                                                            danger: true,
                                                            separatorBefore: true,
                                                            icon: (props) => <FaIcon {...props} className="fas fa-trash-alt" />,
                                                            onSelect: () => removeSurvey(survey.id)
                                                        }
                                                    ]}
                                                />
                                            </div>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>

                    {listPages > 1 && (
                        <div className="flex items-center justify-between gap-2 border-t border-slate-200/70 px-3 py-2.5">
                            <button
                                type="button"
                                onClick={() => setListPage((page) => Math.max(1, page - 1))}
                                disabled={listPage <= 1 || isLoading}
                                className={`${iosBtnGhost} disabled:opacity-40`}
                            >
                                <FaIcon className="fas fa-chevron-left text-[11px]" />
                                Назад
                            </button>
                            <span className="text-[12px] tabular-nums text-slate-500">
                                {listPage} / {listPages}
                            </span>
                            <button
                                type="button"
                                onClick={() => setListPage((page) => Math.min(listPages, page + 1))}
                                disabled={listPage >= listPages || isLoading}
                                className={`${iosBtnGhost} disabled:opacity-40`}
                            >
                                Вперёд
                                <FaIcon className="fas fa-chevron-right text-[11px]" />
                            </button>
                        </div>
                    )}
                </div>

                {/* Survey detail */}
                <div className={`xl:col-span-3 ${iosCard} flex flex-col overflow-hidden`}>
                    {!selectedSurvey ? (
                        <div className="flex flex-1 items-center justify-center p-12 text-center">
                            <div>
                                <div className="mx-auto mb-3 grid h-14 w-14 place-items-center rounded-2xl bg-slate-50">
                                    <FaIcon className={`fas ${isDetailLoading ? 'fa-spinner fa-spin' : 'fa-hand-point-left'} text-2xl text-slate-300`} />
                                </div>
                                <p className="text-[13px] text-slate-400">
                                    {isDetailLoading ? 'Открываем опрос…' : 'Выберите опрос из списка'}
                                </p>
                            </div>
                        </div>
                    ) : (
                        <>
                            {/* Detail header */}
                            <div className="space-y-3 border-b border-slate-200/70 px-5 py-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h3 className="text-[15px] font-bold text-slate-900">{selectedSurvey.title}</h3>
                                            {selectedSurvey?.is_test && <Badge color="blue">Тест</Badge>}
                                            {/* У архивного теста окно уже не считается: «Активен»
                                                рядом с «В архиве» читался бы как противоречие. */}
                                            {selectedSurvey?.is_test && !selectedSurvey?.is_archived && testStatusMeta(liveTestStatus) && (
                                                <Badge color={testStatusMeta(liveTestStatus).color}>
                                                    <FaIcon className={`fas ${testStatusMeta(liveTestStatus).icon} mr-1 text-[9px]`} />
                                                    {testStatusMeta(liveTestStatus).label}
                                                </Badge>
                                            )}
                                            {Number(selectedSurvey?.repeat?.iteration || 1) > 1 && (
                                                <Badge color="blue">Повторение #{Number(selectedSurvey.repeat.iteration)}</Badge>
                                            )}
                                            {selectedSurvey?.is_archived && (
                                                <Badge color="gray">
                                                    <FaIcon className="fas fa-box-archive mr-1 text-[9px]" />
                                                    В архиве
                                                </Badge>
                                            )}
                                            {/* Длинное пояснение — под «i»: на экране оно нужно один
                                                раз, а место занимало бы всегда. */}
                                            {selectedSurvey.description && (
                                                <IosHint text={selectedSurvey.description} label="Описание опроса" />
                                            )}
                                        </div>
                                        <div className="mt-1 text-[11.5px] text-slate-400">
                                            {selectedSurvey?.is_archived
                                                ? `В архиве с ${formatSurveyDateTime(selectedSurvey?.archived_at)}`
                                                : `Создан ${formatSurveyDateTime(selectedSurvey?.created_at)}`}
                                        </div>
                                    </div>

                                    {/* Действия карточки, включая выгрузку: наверху и на виду,
                                        независимо от того, какая вкладка открыта. */}
                                    {canManage && (
                                        <div className="flex shrink-0 items-center gap-2">
                                            <button
                                                type="button"
                                                onClick={exportSurveyStatsExcel}
                                                disabled={isStatsExporting || !selectedSurvey?.id}
                                                className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-50 px-3 py-2 text-[12.5px] font-semibold text-emerald-700 transition-all hover:bg-emerald-100 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
                                                title="Выгрузить результаты в Excel"
                                            >
                                                <FaIcon className={`fas ${isStatsExporting ? 'fa-spinner fa-spin' : 'fa-file-excel'}`} />
                                                {isStatsExporting ? 'Экспорт…' : 'Excel'}
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => startEditSurvey(selectedSurvey)}
                                                className={iosBtnGhost}
                                                title="Редактировать опрос"
                                            >
                                                <FaIcon className="fas fa-edit" />
                                                Редактировать
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => startRepeatSurvey(selectedSurvey)}
                                                className={iosBtnGhost}
                                                title="Создать повтор опроса"
                                            >
                                                <FaIcon className="fas fa-redo" />
                                                Повторить
                                            </button>
                                        </div>
                                    )}
                                    {isOperator && (() => {
                                        const completed = selectedSurvey?.my_assignment?.status === 'completed';
                                        // «Назначен» на архивном опросе звучало бы как «ещё надо
                                        // пройти», хотя пройти его уже нельзя.
                                        const label = completed
                                            ? 'Пройден'
                                            : (selectedSurvey?.is_archived ? 'Не пройден' : 'Назначен');
                                        return <Badge color={completed ? 'green' : (selectedSurvey?.is_archived ? 'gray' : 'amber')}>{label}</Badge>;
                                    })()}
                                </div>

                                {/* Окно теста: одно место, где видно расписание и правила */}
                                {selectedSurvey?.is_test && (
                                    <div className="flex flex-wrap items-center gap-2">
                                        {(selectedTestInfo?.starts_at || selectedTestInfo?.ends_at) && (
                                            <div className="flex items-center gap-1.5 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11.5px] text-slate-500">
                                                <FaIcon className="fas fa-hourglass-start text-[10px] text-slate-400" />
                                                <span className="tabular-nums text-slate-700">{formatSurveyDateTime(selectedTestInfo?.starts_at)}</span>
                                                <span className="text-slate-300">→</span>
                                                <span className="tabular-nums text-slate-700">{formatSurveyDateTime(selectedTestInfo?.ends_at)}</span>
                                            </div>
                                        )}
                                        <div className="flex items-center gap-1.5 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11.5px] text-slate-500">
                                            <FaIcon className="fas fa-star text-[10px] text-slate-400" />
                                            Максимум: <strong className="tabular-nums text-slate-700">{formatPoints(selectedTestInfo?.max_points)}</strong>
                                        </div>
                                        {selectedTestInfo?.single_attempt !== false && (
                                            <Badge color="gray">Одна попытка</Badge>
                                        )}
                                        {selectedTestInfo?.affects_quality && (
                                            <Badge color="blue">Идёт в качество</Badge>
                                        )}
                                    </div>
                                )}

                                {/* Сводка одной строкой: раньше набор плашек менялся вместе
                                    со вкладкой и на каждой был свой — это и был лишний шум. */}
                                <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11.5px] text-slate-500">
                                    {canManage && (
                                        <>
                                            <span className="inline-flex items-center gap-1.5">
                                                <FaIcon className="fas fa-users text-[10px] text-slate-400" />
                                                Сотрудников: <strong className="tabular-nums text-slate-700">{selectedSurveyDisplayMetrics.assignedCount || 0}</strong>
                                            </span>
                                            <span className="inline-flex items-center gap-1.5">
                                                <FaIcon className="fas fa-check-circle text-[10px] text-slate-400" />
                                                Прошли: <strong className="tabular-nums text-slate-700">{selectedSurveyDisplayMetrics.completedCount || 0}</strong>
                                            </span>
                                        </>
                                    )}
                                    <span className="inline-flex items-center gap-1.5">
                                        <FaIcon className="fas fa-list-ul text-[10px] text-slate-400" />
                                        Вопросов: <strong className="tabular-nums text-slate-700">{(selectedSurvey?.questions || []).length}</strong>
                                    </span>
                                    <IosHint
                                        align="right"
                                        label="Кому назначен опрос"
                                        text={`Стаж: ${
                                            selectedSurvey?.assignment?.tenure_weeks_min != null || selectedSurvey?.assignment?.tenure_weeks_max != null
                                                ? `${selectedSurvey?.assignment?.tenure_weeks_min != null ? `от ${selectedSurvey.assignment.tenure_weeks_min} нед.` : 'без минимума'}${selectedSurvey?.assignment?.tenure_weeks_max != null ? ` до ${selectedSurvey.assignment.tenure_weeks_max} нед.` : ''}`
                                                : 'любой'
                                        }. Обязательных вопросов: ${(selectedSurvey?.questions || []).filter((question) => question?.required).length}. С полем «Другое»: ${(selectedSurvey?.questions || []).filter((question) => question?.allow_other).length}.${
                                            canManage ? ` Ещё не прошли: ${selectedSurveyDisplayMetrics.pendingCount || 0}.` : ''
                                        }`}
                                    />
                                </div>

                                {/* Вкладки. Крупный сегментный контрол во всю ширину:
                                    прежние мелкие пилюли читались как подпись, и люди
                                    не понимали, что по ним надо нажимать. */}
                                {canManage && (
                                    <IosSegmented
                                        size="lg"
                                        value={activeTab}
                                        onChange={(tab) => {
                                            // Уходя со вкладки, закрываем разбор: иначе
                                            // возврат на «Ответы» показывал бы чужую
                                            // карточку вместо списка.
                                            if (tab !== 'answers') setOpenedRespondentKey(null);
                                            setActiveTab(tab);
                                        }}
                                        ariaLabel="Разделы опроса"
                                        options={[
                                            {
                                                value: 'questions',
                                                label: 'Вопросы',
                                                icon: <FaIcon className="fas fa-question-circle text-[12px]" />
                                            },
                                            {
                                                value: 'answers',
                                                label: 'Ответы',
                                                icon: <FaIcon className="fas fa-user-check text-[12px]" />,
                                                count: respondentsTotalCount
                                            },
                                            {
                                                value: 'stats',
                                                label: 'Статистика',
                                                icon: <FaIcon className="fas fa-chart-bar text-[12px]" />
                                            }
                                        ]}
                                    />
                                )}
                            </div>

                            {/* Detail body */}
                            <div className="thin-scroll flex-1 space-y-3 overflow-y-auto p-5">

                                {/* Опрос в архиве, а сотрудник его не проходил.
                                    Без этой строки на месте формы оставалась
                                    пустая карточка, и было непонятно, сломалось
                                    что-то или так и задумано. */}
                                {isOperator && selectedSurvey?.is_archived && !isSelectedSurveyCompleted && (
                                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                                        <div className="flex items-start gap-3">
                                            <FaIcon className="fas fa-box-archive mt-0.5 text-slate-400" />
                                            <div className="text-[13.5px] text-slate-700">
                                                <div className="font-semibold">Опрос ушёл в архив</div>
                                                <div className="mt-1 text-[13px] text-slate-500">
                                                    Прошло больше двух недель с его создания — пройти опрос уже нельзя.
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                )}

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
                                        {/* Шапка прохождения, как на тестовых сайтах: сколько
                                            вопросов позади и сколько осталось времени. Без неё
                                            в длинном тесте непонятно, где ты и успеваешь ли. */}
                                        <div className="sticky top-0 z-10 -mx-1 space-y-2 rounded-xl bg-white/90 px-3.5 py-2.5 ring-1 ring-slate-200/70 backdrop-blur">
                                            <div className="flex items-center justify-between gap-3">
                                                <div className="text-[13px] text-slate-600">
                                                    Отвечено{' '}
                                                    <strong className="tabular-nums text-slate-900">
                                                        {fillProgress.answered} из {fillProgress.total}
                                                    </strong>
                                                </div>
                                                {selectedSurvey?.is_test && testMsLeft != null && (
                                                    <div className={`flex items-center gap-2 text-[15px] font-semibold tabular-nums ${
                                                        testMsLeft <= 60000 ? 'text-red-600' : 'text-slate-800'
                                                    }`}>
                                                        <FaIcon className={`fas fa-stopwatch text-[11px] ${testMsLeft <= 60000 ? 'text-red-500' : 'text-slate-400'}`} />
                                                        {formatCountdown(testMsLeft)}
                                                    </div>
                                                )}
                                            </div>
                                            <ProgressBar
                                                value={fillProgress.percent}
                                                color={fillProgress.answered === fillProgress.total ? 'emerald' : 'blue'}
                                            />
                                        </div>
                                        {(selectedSurvey.questions || []).map((question, index) => {
                                            const answer = answers[question.id] || {};
                                            const isAnswered = isFillAnswerFilled(question, answer);
                                            return (
                                                <div key={question.id} className={`space-y-3 rounded-2xl bg-white p-4 ring-1 transition-colors ${
                                                    isAnswered ? 'ring-slate-200/70' : 'ring-slate-200'
                                                }`}>
                                                    <div className="flex items-start justify-between gap-2">
                                                        <div>
                                                            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
                                                                <span className={`grid h-[18px] w-[18px] place-items-center rounded-full text-[9px] font-bold ${
                                                                    isAnswered ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'
                                                                }`}>
                                                                    {isAnswered ? <FaIcon className="fas fa-check" /> : index + 1}
                                                                </span>
                                                                {questionTypeLabel(question.type)}
                                                                {question.required && <span className="text-red-400">*</span>}
                                                            </div>
                                                            <div className="text-[14px] font-medium text-slate-900">{question.text}</div>
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
                                                                                : 'border-slate-200 text-slate-500 hover:border-amber-300 hover:text-amber-500'
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
                                                                        className={`flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 ring-1 transition-all active:scale-[0.995] ${
                                                                            selected
                                                                                ? 'bg-blue-50 ring-blue-300'
                                                                                : 'bg-white ring-slate-200/70 hover:ring-slate-300'
                                                                        }`}
                                                                    >
                                                                        <div className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center transition-all ${
                                                                            question.type === 'single'
                                                                                ? `rounded-full border-2 ${selected ? 'border-blue-600' : 'border-slate-300'}`
                                                                                : `rounded-[5px] border-2 ${selected ? 'bg-blue-600 border-blue-600' : 'border-slate-300'}`
                                                                        }`}>
                                                                            {selected && question.type === 'single' && <div className="h-2 w-2 rounded-full bg-blue-600" />}
                                                                            {selected && question.type === 'multiple' && <FaIcon className="fas fa-check text-[9px] text-white" />}
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
                                                                        <span className="text-[13.5px] text-slate-800">{option}</span>
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
                                                                        className={iosInput}
                                                                    />
                                                                    <div className="text-[10px] text-slate-400 text-right">
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
                                            <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 text-sm text-slate-400">
                                                В этом опросе нет сохраненных вопросов.
                                            </div>
                                        )}
                                        {(selectedSurvey.questions || []).map((question, index) => {
                                            const normalizedOptions = toUniqueTrimmedList(question.options);
                                            const isTestQuestions = !!selectedSurvey?.is_test && question.type !== 'rating';
                                            const correctOptions = toUniqueTrimmedList(question.correct_options);
                                            return (
                                                <div key={question.id} className="flex gap-3 items-start p-3 rounded-xl border border-slate-100 bg-slate-50/60">
                                                    <div className="w-6 h-6 rounded-lg bg-blue-50 flex items-center justify-center shrink-0 mt-0.5">
                                                        <span className="text-[10px] font-bold text-blue-500">{index + 1}</span>
                                                    </div>
                                                    <div className="flex-1">
                                                        <div className="text-sm font-medium text-slate-800">{question.text}</div>
                                                        <div className="mt-1 flex flex-wrap items-center gap-2">
                                                            <Badge color="gray">{questionTypeLabel(question.type)}</Badge>
                                                            {question.required && <Badge color="blue">Обязательный</Badge>}
                                                            {isTestQuestions && (
                                                                <span className="text-[11px] tabular-nums text-slate-500">
                                                                    {formatPoints(question.points)} балл.
                                                                    {question.partial_credit && ' · частичный зачёт'}
                                                                </span>
                                                            )}
                                                        </div>
                                                        {question.type !== 'rating' && (
                                                            <div className="mt-2 space-y-1.5">
                                                                {normalizedOptions.length > 0 ? (
                                                                    /* В тесте правильный вариант отмечаем прямо в списке,
                                                                       как на любом тестовом сайте. Отдельная строка
                                                                       «Правильный ответ: Город, Номер телефона» заставляла
                                                                       сверять глазами два списка — и была вторым местом,
                                                                       где те же значения повторялись. */
                                                                    <div className="space-y-1.5">
                                                                        {normalizedOptions.map((option) => {
                                                                            const isCorrect = isTestQuestions && correctOptions.includes(option);
                                                                            return (
                                                                                <div
                                                                                    key={`${question.id}_${option}`}
                                                                                    className={`flex items-center gap-2.5 rounded-xl px-3 py-2 ring-1 ${
                                                                                        isCorrect ? 'bg-emerald-50 ring-emerald-200' : 'bg-white ring-slate-200/70'
                                                                                    }`}
                                                                                >
                                                                                    <span className={`grid h-[18px] w-[18px] shrink-0 place-items-center border-2 text-[9px] ${
                                                                                        question.type === 'multiple' ? 'rounded-[5px]' : 'rounded-full'
                                                                                    } ${isCorrect ? 'border-emerald-500 bg-emerald-500 text-white' : 'border-slate-300 text-transparent'}`}>
                                                                                        <FaIcon className="fas fa-check" />
                                                                                    </span>
                                                                                    <span className={`min-w-0 flex-1 break-words text-[13px] ${isCorrect ? 'text-emerald-900' : 'text-slate-700'}`}>
                                                                                        {option}
                                                                                    </span>
                                                                                    {isCorrect && (
                                                                                        <span className="shrink-0 text-[11px] font-medium text-emerald-700">Правильный</span>
                                                                                    )}
                                                                                </div>
                                                                            );
                                                                        })}
                                                                        {question.allow_other && (
                                                                            <div className="rounded-xl bg-amber-50 px-3 py-2 text-[12.5px] text-amber-700 ring-1 ring-amber-200">
                                                                                Плюс свободное поле «Другое»
                                                                            </div>
                                                                        )}
                                                                    </div>
                                                                ) : (
                                                                    <div className="text-xs text-slate-500">
                                                                        {question.allow_other ? 'Только поле «Другое»' : 'Без фиксированных вариантов'}
                                                                    </div>
                                                                )}
                                                                {isTestQuestions && correctOptions.length === 0 && (
                                                                    <div className="text-[12px] text-amber-600">Правильный вариант не отмечен</div>
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
                                            <div className="text-xs text-slate-500">
                                                Отправлено:{' '}
                                                <strong className="text-slate-700 tabular-nums">
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
                                        {/* Свой результат — тем же разбором, что видит руководитель:
                                            все варианты, отмечено выбранное и правильное. */}
                                        <AttemptReview
                                            selfView
                                            isTest={!!selectedSurvey?.is_test}
                                            questions={selectedSurvey.questions || []}
                                            getAnswer={(question) => {
                                                const byQuestion = selectedSurvey?.my_response?.answers_by_question || {};
                                                return byQuestion[String(question.id)] || byQuestion[question.id] || null;
                                            }}
                                        />
                                    </div>
                                )}

                                {/* Manager answers tab.
                                    Два состояния на одном месте: сетка карточек и
                                    разбор одного человека во всю область. Разбор
                                    НЕ модалка и не аккордеон внутри строки: у теста
                                    на 25 вопросов ему нужен весь экран, а внутри
                                    строки он ужимался бы в щель между соседями. */}
                                {canManage && activeTab === 'answers' && !openedRespondent && (
                                    <div className="animate-card-open space-y-3">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-500">
                                                <span>
                                                    Прошли:{' '}
                                                    <strong className="tabular-nums text-slate-800">{respondentsSummary.completedCount}</strong>
                                                </span>
                                                <span>
                                                    Ещё нет:{' '}
                                                    <strong className="tabular-nums text-slate-800">{respondentsSummary.pendingCount}</strong>
                                                </span>
                                                {isTestStatsSurvey && respondentsSummary.averageScore != null && (
                                                    <span>
                                                        Средний результат:{' '}
                                                        <strong className="tabular-nums text-slate-800">
                                                            {formatPercent(respondentsSummary.averageScore)}
                                                        </strong>
                                                    </span>
                                                )}
                                            </div>
                                            <div className="relative w-full max-w-[240px]">
                                                <FaIcon className="fas fa-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[12px] text-slate-400" />
                                                <input
                                                    value={statsOperatorQuery}
                                                    onChange={(e) => setStatsOperatorQuery(e.target.value)}
                                                    placeholder="Поиск по сотруднику"
                                                    className={`${iosInput} py-2 pl-8 text-[13px]`}
                                                />
                                            </div>
                                        </div>

                                        {respondentCards.length === 0 && (
                                            <div className="py-10 text-center">
                                                <FaIcon className="fas fa-user-check mb-2 block text-3xl text-slate-200" />
                                                <p className="text-[13px] text-slate-400">
                                                    {statsOperatorQuery ? 'Сотрудники не найдены' : 'Ответов пока нет'}
                                                </p>
                                            </div>
                                        )}

                                        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                                            {respondentCards.map((card) => {
                                                const scoreColor = card.scoreValue == null
                                                    ? 'text-slate-400'
                                                    : (card.scoreValue >= 80
                                                        ? 'text-emerald-600'
                                                        : (card.scoreValue >= 60 ? 'text-blue-600' : 'text-amber-600'));
                                                return (
                                                    <button
                                                        key={card.key}
                                                        type="button"
                                                        disabled={!card.isCompleted}
                                                        onClick={() => setOpenedRespondentKey(card.key)}
                                                        className={`flex min-h-[92px] flex-col justify-between rounded-2xl px-4 py-3.5 text-left ring-1 transition-all duration-200 ${
                                                            card.isCompleted
                                                                ? 'bg-white ring-slate-200/70 hover:-translate-y-0.5 hover:shadow-[0_6px_20px_-12px_rgba(15,23,42,0.4)] hover:ring-blue-300 active:scale-[0.99]'
                                                                : 'cursor-default bg-slate-50 ring-slate-200/60'
                                                        }`}
                                                    >
                                                        <div className={`text-[13.5px] font-semibold leading-snug ${card.isCompleted ? 'text-slate-900' : 'text-slate-500'}`}>
                                                            {card.name}
                                                        </div>
                                                        <div className="mt-2 flex items-end justify-between gap-2">
                                                            <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                                                                {!card.isCompleted && <Badge color="amber">Не проходил</Badge>}
                                                                {card.isDismissed && <Badge color="gray">Уволен</Badge>}
                                                                {card.repeatIteration > 1 && <Badge color="blue">#{card.repeatIteration}</Badge>}
                                                            </div>
                                                            {card.isCompleted && (
                                                                isTestStatsSurvey && card.hasScore ? (
                                                                    <span className={`shrink-0 text-[19px] font-bold leading-none tabular-nums ${scoreColor}`}>
                                                                        {formatPercent(card.scoreValue)}
                                                                    </span>
                                                                ) : (
                                                                    <span className="shrink-0 text-[11.5px] tabular-nums text-slate-500">
                                                                        Ответов {card.answeredCount} из {card.questionsCount}
                                                                    </span>
                                                                )
                                                            )}
                                                        </div>
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    </div>
                                )}

                                {canManage && activeTab === 'answers' && openedRespondent && (
                                    <div className="animate-card-open space-y-3">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <button
                                                type="button"
                                                onClick={() => setOpenedRespondentKey(null)}
                                                className={iosBtnGhost}
                                            >
                                                <FaIcon className="fas fa-chevron-left text-[11px]" />
                                                Назад к списку
                                            </button>
                                            <span className="hidden text-[11.5px] text-slate-400 sm:block">
                                                Esc — вернуться
                                            </span>
                                        </div>

                                        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200/70 pb-3">
                                            <div className="min-w-0">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className="text-[15px] font-bold text-slate-900">{openedRespondent.name}</span>
                                                    {openedRespondent.isDismissed && <Badge color="gray">Уволен</Badge>}
                                                    {openedRespondent.repeatIteration > 1 && <Badge color="blue">#{openedRespondent.repeatIteration}</Badge>}
                                                    {openedRespondent.testSummary?.is_auto_submitted && (
                                                        <Badge color="amber">Отправлено по времени</Badge>
                                                    )}
                                                </div>
                                                <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-500">
                                                    {isTestStatsSurvey ? (
                                                        <>
                                                            <span>
                                                                Верно:{' '}
                                                                <strong className="tabular-nums text-slate-800">
                                                                    {Number(openedRespondent.testSummary?.correct_answers || 0)} из {Number(openedRespondent.testSummary?.total_questions || 0)}
                                                                </strong>
                                                            </span>
                                                            {openedRespondent.hasScore && (
                                                                <span>
                                                                    Баллы:{' '}
                                                                    <strong className="tabular-nums text-slate-800">
                                                                        {formatPoints(openedRespondent.testSummary?.earned_points)} / {formatPoints(openedRespondent.testSummary?.max_points)}
                                                                    </strong>
                                                                </span>
                                                            )}
                                                        </>
                                                    ) : (
                                                        <span>
                                                            Ответов:{' '}
                                                            <strong className="tabular-nums text-slate-800">
                                                                {openedRespondent.answeredCount} из {openedRespondent.questionsCount}
                                                            </strong>
                                                        </span>
                                                    )}
                                                    <span className="tabular-nums text-slate-400">
                                                        {formatSurveyDateTime(openedRespondent.submittedAt)}
                                                    </span>
                                                </div>
                                            </div>
                                            {isTestStatsSurvey && openedRespondent.hasScore && (
                                                <div className={`shrink-0 text-[26px] font-bold leading-none tabular-nums ${
                                                    openedRespondent.scoreValue >= 80
                                                        ? 'text-emerald-600'
                                                        : (openedRespondent.scoreValue >= 60 ? 'text-blue-600' : 'text-amber-600')
                                                }`}>
                                                    {formatPercent(openedRespondent.scoreValue)}
                                                </div>
                                            )}
                                        </div>

                                        <AttemptReview
                                            questions={openedRespondent.questions}
                                            isTest={isTestStatsSurvey}
                                            getAnswer={(question, questionIndex) => {
                                                const resolved = resolveStatsQuestionAndAnswer(openedRespondent.row, question, questionIndex);
                                                return resolved.answer
                                                    ? { ...resolved.answer, __question: resolved.question }
                                                    : null;
                                            }}
                                        />
                                    </div>
                                )}

                                {/* Manager stats tab */}
                                {canManage && activeTab === 'stats' && (
                                    <div className="space-y-3">
                                        {/* Сводка сверху: до неё, чтобы понять «как прошло»,
                                            приходилось складывать проценты по вопросам глазами. */}
                                        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                                            {[
                                                {
                                                    key: 'assigned',
                                                    label: 'Назначено',
                                                    value: selectedSurveyDisplayMetrics.assignedCount || 0
                                                },
                                                {
                                                    key: 'completed',
                                                    label: 'Прошли',
                                                    value: selectedSurveyDisplayMetrics.completedCount || 0
                                                },
                                                {
                                                    key: 'rate',
                                                    label: 'Доля прохождения',
                                                    value: formatPercent(selectedSurveyDisplayMetrics.completionRate || 0)
                                                },
                                                isTestStatsSurvey
                                                    ? {
                                                        key: 'score',
                                                        label: 'Средний результат',
                                                        value: respondentsSummary.averageScore != null
                                                            ? formatPercent(respondentsSummary.averageScore)
                                                            : '—'
                                                    }
                                                    : {
                                                        key: 'questions',
                                                        label: 'Вопросов',
                                                        value: (selectedSurvey?.questions || []).length
                                                    }
                                            ].map((tile) => (
                                                <div key={tile.key} className="rounded-2xl bg-white px-3.5 py-3 ring-1 ring-slate-200/70">
                                                    <div className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">
                                                        {tile.label}
                                                    </div>
                                                    <div className="mt-1 text-[19px] font-semibold tabular-nums leading-none text-slate-900">
                                                        {tile.value}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>

                                        {displayQuestionStats.length === 0 && (
                                            <div className="py-10 text-center">
                                                <FaIcon className="fas fa-chart-bar mb-2 block text-3xl text-slate-200" />
                                                <p className="text-[13px] text-slate-400">Данных для статистики пока нет</p>
                                            </div>
                                        )}
                                        {displayQuestionStats.map((stat, index) => renderDetailedQuestionStats(stat, index))}
                                    </div>
                                )}
                            </div>
                        </>
                    )}
                </div>
            </div>
            </>
            )}
        </div>
    );
};

export default SurveysView;
