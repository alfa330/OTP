import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle,
    Award,
    Ban,
    Bell,
    BookOpen,
    CalendarDays,
    Check,
    CheckCheck,
    CheckCircle2,
    ClipboardList,
    Clock3,
    Download,
    FileText,
    FolderOpen,
    Gauge,
    GraduationCap,
    Link2,
    Loader2,
    Pause,
    Play,
    RefreshCw,
    Search,
    ShieldCheck,
    Target,
    Timer,
    TimerReset,
    Upload,
    Users,
    Video,
    X
} from 'lucide-react';

const LEARNER_ROLES = new Set(['operator', 'trainee']);
const MANAGER_ROLES = new Set(['sv', 'trainer', 'admin', 'super_admin']);
const FULL_ADMIN_ROLES = new Set(['admin', 'super_admin']);

const HEARTBEAT_FALLBACK_SECONDS = 15;
const STALE_GAP_FALLBACK_SECONDS = 45;
const COMPLETION_THRESHOLD_FALLBACK = 95;

const STATUS_LABELS = {
    assigned: 'Назначен',
    in_progress: 'В процессе',
    completed: 'Завершен',
    draft: 'Черновик',
    published: 'Опубликован',
    archived: 'Архив',
    overdue: 'Просрочен'
};

const DEADLINE_META = {
    on_time: {
        label: 'В срок',
        badge: 'bg-emerald-100 text-emerald-700 border border-emerald-200'
    },
    late_completed: {
        label: 'Сдано с опозданием',
        badge: 'bg-amber-100 text-amber-700 border border-amber-200'
    },
    overdue: {
        label: 'Просрочено',
        badge: 'bg-rose-100 text-rose-700 border border-rose-200'
    },
    unknown: {
        label: 'Без срока',
        badge: 'bg-slate-100 text-slate-700 border border-slate-200'
    }
};

const safeJson = (value) => {
    try {
        return JSON.stringify(value ?? null);
    } catch {
        return '';
    }
};

const toNumber = (value, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
};

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const formatPercent = (value) => `${toNumber(value, 0).toFixed(0)}%`;

const formatDateTime = (isoValue) => {
    if (!isoValue) return '—';
    const date = new Date(isoValue);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('ru-RU', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
};

const formatDuration = (value) => {
    const total = Math.max(0, Math.floor(toNumber(value, 0)));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};

const deadlineMetaFor = (status) => DEADLINE_META[status] || DEADLINE_META.unknown;

const statusLabel = (status) => STATUS_LABELS[status] || status || '—';

const isAbortError = (error) => {
    if (!error) return false;
    if (error.code === 'ERR_CANCELED') return true;
    if (error.name === 'CanceledError') return true;
    if (typeof axios.isCancel === 'function' && axios.isCancel(error)) return true;
    return false;
};

const materialIcon = (type) => {
    switch (String(type || '').toLowerCase()) {
    case 'video':
        return Video;
    case 'pdf':
    case 'text':
        return FileText;
    case 'link':
        return Link2;
    default:
        return FolderOpen;
    }
};

const parseSelectedIds = (payload) => {
    if (!payload || typeof payload !== 'object') return [];
    if (Array.isArray(payload.option_ids)) return payload.option_ids.map((x) => toNumber(x, 0)).filter(Boolean);
    if (payload.option_id !== undefined && payload.option_id !== null) {
        const id = toNumber(payload.option_id, 0);
        return id ? [id] : [];
    }
    if (Array.isArray(payload.value)) return payload.value.map((x) => toNumber(x, 0)).filter(Boolean);
    if (payload.value !== undefined && payload.value !== null) {
        const id = toNumber(payload.value, 0);
        return id ? [id] : [];
    }
    return [];
};

const extractMatchingPairs = (payload) => {
    if (!payload || typeof payload !== 'object') return {};
    if (payload.pairs && typeof payload.pairs === 'object' && !Array.isArray(payload.pairs)) {
        return Object.entries(payload.pairs).reduce((acc, [left, right]) => {
            if (!left) return acc;
            acc[String(left)] = String(right ?? '');
            return acc;
        }, {});
    }
    if (Array.isArray(payload.pairs)) {
        return payload.pairs.reduce((acc, pair) => {
            if (!pair || typeof pair !== 'object') return acc;
            if (!pair.left) return acc;
            acc[String(pair.left)] = String(pair.right ?? '');
            return acc;
        }, {});
    }
    return {};
};

const buildMatchingRightChoices = (question) => {
    const metadata = question?.metadata && typeof question.metadata === 'object' ? question.metadata : {};
    if (Array.isArray(metadata.right_options) && metadata.right_options.length > 0) {
        return metadata.right_options
            .map((item) => {
                if (item && typeof item === 'object') {
                    const value = String(item.value ?? item.key ?? item.id ?? '').trim();
                    const label = String(item.label ?? item.text ?? item.value ?? '').trim();
                    if (!value) return null;
                    return { value, label: label || value };
                }
                const text = String(item ?? '').trim();
                if (!text) return null;
                return { value: text, label: text };
            })
            .filter(Boolean);
    }

    const options = Array.isArray(question?.options) ? question.options : [];
    const derived = options
        .map((opt) => {
            const value = String(opt?.metadata?.right_key ?? opt?.metadata?.right ?? opt?.key ?? '').trim();
            const label = String(opt?.metadata?.right_label ?? opt?.metadata?.right_text ?? opt?.text ?? value).trim();
            if (!value) return null;
            return { value, label: label || value };
        })
        .filter(Boolean);

    const unique = [];
    const seen = new Set();
    derived.forEach((item) => {
        if (!seen.has(item.value)) {
            seen.add(item.value);
            unique.push(item);
        }
    });
    return unique;
};

const SectionCard = ({ title, subtitle, actions, children }) => (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
                <h3 className="text-base font-semibold text-slate-900">{title}</h3>
                {subtitle ? <p className="mt-1 text-sm text-slate-500">{subtitle}</p> : null}
            </div>
            {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
        </div>
        {children}
    </section>
);

const MetricCard = ({ icon: Icon, label, value, helper }) => (
    <div className="rounded-2xl border border-slate-200 bg-white/95 p-4 shadow-sm">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <Icon className="h-4 w-4 text-slate-500" />
            <span>{label}</span>
        </div>
        <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
        {helper ? <div className="mt-1 text-xs text-slate-500">{helper}</div> : null}
    </div>
);

const TabButton = ({ icon: Icon, label, active, onClick }) => (
    <button
        type="button"
        onClick={onClick}
        className={`inline-flex items-center gap-2 rounded-xl border px-4 py-2 text-sm font-medium transition ${
            active
                ? 'border-blue-700 bg-blue-700 text-white shadow'
                : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50'
        }`}
    >
        <Icon className="h-4 w-4" />
        <span>{label}</span>
    </button>
);

const QuestionEditor = ({ question, value, savingState, onChange }) => {
    const qType = String(question?.type || '').toLowerCase();
    const options = Array.isArray(question?.options) ? question.options : [];

    if (qType === 'single') {
        const selectedId = parseSelectedIds(value)[0] || 0;
        return (
            <div className="space-y-2">
                {options.map((option) => {
                    const selected = selectedId === toNumber(option.id, 0);
                    return (
                        <button
                            key={option.id}
                            type="button"
                            onClick={() => onChange({ option_id: option.id }, true)}
                            className={`flex w-full items-start gap-3 rounded-xl border px-3 py-2 text-left text-sm transition ${
                                selected
                                    ? 'border-blue-400 bg-blue-50 text-blue-900'
                                    : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300'
                            }`}
                        >
                            <span className={`mt-0.5 inline-flex h-4 w-4 rounded-full border ${selected ? 'border-blue-600 bg-blue-600' : 'border-slate-400 bg-white'}`} />
                            <span>{option.text}</span>
                        </button>
                    );
                })}
            </div>
        );
    }

    if (qType === 'multiple') {
        const selected = new Set(parseSelectedIds(value));
        return (
            <div className="space-y-2">
                {options.map((option) => {
                    const optionId = toNumber(option.id, 0);
                    const active = selected.has(optionId);
                    return (
                        <button
                            key={option.id}
                            type="button"
                            onClick={() => {
                                const next = new Set(selected);
                                if (active) next.delete(optionId);
                                else next.add(optionId);
                                onChange({ option_ids: Array.from(next) }, true);
                            }}
                            className={`flex w-full items-start gap-3 rounded-xl border px-3 py-2 text-left text-sm transition ${
                                active
                                    ? 'border-cyan-400 bg-cyan-50 text-cyan-900'
                                    : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300'
                            }`}
                        >
                            <span className={`mt-0.5 inline-flex h-4 w-4 items-center justify-center rounded border ${active ? 'border-cyan-600 bg-cyan-600 text-white' : 'border-slate-400 bg-white'}`}>
                                {active ? <Check className="h-3 w-3" /> : null}
                            </span>
                            <span>{option.text}</span>
                        </button>
                    );
                })}
            </div>
        );
    }

    if (qType === 'true_false') {
        const selectedId = parseSelectedIds(value)[0] || 0;
        const fallbackOptions = [
            { id: 'true', text: 'Верно', boolValue: true },
            { id: 'false', text: 'Неверно', boolValue: false }
        ];
        const source = options.length > 0 ? options : fallbackOptions;
        return (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {source.map((option) => {
                    const optionId = String(option.id);
                    const active = selectedId ? selectedId === toNumber(option.id, 0) : String(value?.value) === String(option.boolValue);
                    return (
                        <button
                            key={optionId}
                            type="button"
                            onClick={() => {
                                if (options.length > 0) {
                                    onChange({ option_id: option.id }, true);
                                } else {
                                    onChange({ value: !!option.boolValue }, true);
                                }
                            }}
                            className={`rounded-xl border px-3 py-2 text-sm font-medium transition ${
                                active
                                    ? 'border-blue-500 bg-blue-600 text-white'
                                    : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300'
                            }`}
                        >
                            {option.text}
                        </button>
                    );
                })}
            </div>
        );
    }

    if (qType === 'matching') {
        const pairs = extractMatchingPairs(value);
        const leftItems = options.filter((option) => String(option?.key || '').trim());
        const rightChoices = buildMatchingRightChoices(question);

        return (
            <div className="space-y-3">
                {leftItems.length === 0 ? (
                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-500">
                        Для этого вопроса не настроены пары.
                    </div>
                ) : (
                    leftItems.map((left) => {
                        const leftKey = String(left.key || '');
                        return (
                            <label key={left.id} className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[1fr_180px]">
                                <span className="text-sm text-slate-700">{left.text}</span>
                                <select
                                    className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 focus:border-blue-500 focus:outline-none"
                                    value={pairs[leftKey] || ''}
                                    onChange={(event) => {
                                        const next = { ...pairs, [leftKey]: event.target.value };
                                        onChange({ pairs: next }, false);
                                    }}
                                >
                                    <option value="">Выберите соответствие</option>
                                    {rightChoices.map((item) => (
                                        <option key={`${left.id}-${item.value}`} value={item.value}>
                                            {item.label}
                                        </option>
                                    ))}
                                </select>
                            </label>
                        );
                    })
                )}
            </div>
        );
    }

    const textValue = typeof value?.text === 'string'
        ? value.text
        : (typeof value?.value === 'string' ? value.value : '');
    return (
        <textarea
            value={textValue}
            onChange={(event) => onChange({ text: event.target.value }, false)}
            placeholder="Введите ответ"
            rows={4}
            className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:border-blue-500 focus:outline-none"
        />
    );
};

const LmsView = ({ user, apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const role = String(user?.role || '').trim().toLowerCase();
    const isLearner = LEARNER_ROLES.has(role);
    const isManager = MANAGER_ROLES.has(role);
    const isFullAdmin = FULL_ADMIN_ROLES.has(role);

    const learnerTabs = useMemo(() => ([
        { id: 'dashboard', label: 'Обзор', icon: Gauge },
        { id: 'courses', label: 'Курсы', icon: BookOpen },
        { id: 'certificates', label: 'Сертификаты', icon: Award },
        { id: 'notifications', label: 'Уведомления', icon: Bell }
    ]), []);

    const managerTabs = useMemo(() => {
        const items = [
            { id: 'admin_courses', label: 'Курсы', icon: GraduationCap },
            { id: 'admin_assignments', label: 'Назначения', icon: Users },
            { id: 'admin_progress', label: 'Прогресс', icon: ClipboardList },
            { id: 'admin_attempts', label: 'Попытки', icon: Target },
            { id: 'admin_deadlines', label: 'Дедлайны', icon: CalendarDays },
            { id: 'admin_materials', label: 'Материалы', icon: Upload }
        ];
        if (isFullAdmin) {
            items.push({ id: 'admin_revoke', label: 'Отзыв сертификата', icon: Ban });
        }
        return items;
    }, [isFullAdmin]);

    const availableTabs = useMemo(() => {
        const tabs = [];
        if (isLearner) tabs.push(...learnerTabs);
        if (isManager) tabs.push(...managerTabs);
        return tabs;
    }, [isLearner, isManager, learnerTabs, managerTabs]);

    const [activeTab, setActiveTab] = useState(() => (isLearner ? 'dashboard' : 'admin_courses'));

    useEffect(() => {
        if (!availableTabs.some((tab) => tab.id === activeTab)) {
            setActiveTab(availableTabs[0]?.id || 'dashboard');
        }
    }, [availableTabs, activeTab]);

    const headers = useMemo(
        () => withAccessTokenHeader({
            'X-API-Key': user?.apiKey,
            'X-User-Id': user?.id
        }),
        [withAccessTokenHeader, user?.apiKey, user?.id]
    );

    const mountedRef = useRef(true);
    const inflightRef = useRef(new Map());
    const cacheRef = useRef(new Map());
    const exclusiveControllersRef = useRef(new Map());
    const heartbeatBusyRef = useRef(false);
    const answerTimersRef = useRef(new Map());
    const answerLastSavedRef = useRef(new Map());
    const answerDirtyRef = useRef(new Set());
    const lastErrorToastAtRef = useRef(0);

    useEffect(() => {
        return () => {
            mountedRef.current = false;
            answerTimersRef.current.forEach((timerId) => window.clearTimeout(timerId));
            answerTimersRef.current.clear();
            exclusiveControllersRef.current.forEach((controller) => controller.abort());
            exclusiveControllersRef.current.clear();
        };
    }, []);

    const request = useCallback(
        async (method, path, options = {}) => {
            const upperMethod = String(method || 'GET').toUpperCase();
            const {
                data = null,
                params = undefined,
                signal = undefined,
                useCache = false,
                dedupe = true,
                cacheTtlMs = 15000,
                responseType = undefined
            } = options;

            const key = `${upperMethod}|${path}|${safeJson(params)}|${safeJson(data)}|${responseType || ''}`;
            const now = Date.now();
            if (upperMethod === 'GET' && useCache) {
                const cached = cacheRef.current.get(key);
                if (cached && cached.expiresAt > now) {
                    return cached.payload;
                }
            }

            const canDedupe = dedupe && !signal;
            if (canDedupe && inflightRef.current.has(key)) {
                return inflightRef.current.get(key);
            }

            const config = {
                method: upperMethod,
                url: `${apiBaseUrl}${path}`,
                headers,
                withCredentials: true,
                params,
                signal,
                responseType
            };
            if (data !== null) {
                config.data = data;
            }

            const promise = axios(config)
                .then((response) => {
                    const payload = responseType ? response : response.data;
                    if (upperMethod === 'GET' && useCache) {
                        cacheRef.current.set(key, {
                            payload,
                            expiresAt: Date.now() + cacheTtlMs
                        });
                    }
                    return payload;
                })
                .finally(() => {
                    inflightRef.current.delete(key);
                });

            if (canDedupe) {
                inflightRef.current.set(key, promise);
            }
            return promise;
        },
        [apiBaseUrl, headers]
    );

    const invalidateLmsCache = useCallback(() => {
        for (const key of Array.from(cacheRef.current.keys())) {
            if (key.includes('/api/lms/')) {
                cacheRef.current.delete(key);
            }
        }
    }, []);

    const runExclusive = useCallback(async (slot, task) => {
        const existing = exclusiveControllersRef.current.get(slot);
        if (existing) existing.abort();
        const controller = new AbortController();
        exclusiveControllersRef.current.set(slot, controller);
        try {
            return await task(controller.signal);
        } finally {
            if (exclusiveControllersRef.current.get(slot) === controller) {
                exclusiveControllersRef.current.delete(slot);
            }
        }
    }, []);

    const showErrorToast = useCallback((message) => {
        const now = Date.now();
        if (now - lastErrorToastAtRef.current < 1000) return;
        lastErrorToastAtRef.current = now;
        showToast?.(message, 'error');
    }, [showToast]);

    const [homeData, setHomeData] = useState(null);
    const [homeLoaded, setHomeLoaded] = useState(false);
    const [homeLoading, setHomeLoading] = useState(false);

    const [courses, setCourses] = useState([]);
    const [coursesLoaded, setCoursesLoaded] = useState(false);
    const [coursesLoading, setCoursesLoading] = useState(false);

    const [selectedCourseId, setSelectedCourseId] = useState(null);
    const [courseDetail, setCourseDetail] = useState(null);
    const [courseDetailLoading, setCourseDetailLoading] = useState(false);

    const [lessonData, setLessonData] = useState(null);
    const [lessonLoading, setLessonLoading] = useState(false);
    const [selectedLessonId, setSelectedLessonId] = useState(null);
    const [player, setPlayer] = useState({
        isPlaying: false,
        positionSeconds: 0,
        maxObservedSeconds: 0,
        seekDraft: null
    });

    const [testAttemptData, setTestAttemptData] = useState(null);
    const [testResult, setTestResult] = useState(null);
    const [testLoading, setTestLoading] = useState(false);
    const [finishingTest, setFinishingTest] = useState(false);
    const [answers, setAnswers] = useState({});
    const [answerSavingState, setAnswerSavingState] = useState({});

    const [certificates, setCertificates] = useState([]);
    const [certificatesLoaded, setCertificatesLoaded] = useState(false);
    const [certificatesLoading, setCertificatesLoading] = useState(false);
    const [downloadingCertificateId, setDownloadingCertificateId] = useState(0);

    const [notifications, setNotifications] = useState([]);
    const [notificationsLoaded, setNotificationsLoaded] = useState(false);
    const [notificationsLoading, setNotificationsLoading] = useState(false);

    const [adminCourses, setAdminCourses] = useState([]);
    const [adminCoursesLoaded, setAdminCoursesLoaded] = useState(false);
    const [adminCoursesLoading, setAdminCoursesLoading] = useState(false);

    const [adminProgress, setAdminProgress] = useState([]);
    const [adminProgressLoaded, setAdminProgressLoaded] = useState(false);
    const [adminProgressLoading, setAdminProgressLoading] = useState(false);

    const [adminAttempts, setAdminAttempts] = useState([]);
    const [adminAttemptsLoaded, setAdminAttemptsLoaded] = useState(false);
    const [adminAttemptsLoading, setAdminAttemptsLoading] = useState(false);

    const [assignableUsers, setAssignableUsers] = useState([]);
    const [assignableUsersLoaded, setAssignableUsersLoaded] = useState(false);
    const [assignableUsersLoading, setAssignableUsersLoading] = useState(false);

    const [courseSearch, setCourseSearch] = useState('');
    const [userSearch, setUserSearch] = useState('');

    const [newCourseForm, setNewCourseForm] = useState({
        title: '',
        description: '',
        category: '',
        pass_threshold: 80,
        attempt_limit: 3,
        blueprint_json: ''
    });
    const [createCourseLoading, setCreateCourseLoading] = useState(false);

    const [assignmentForm, setAssignmentForm] = useState({
        course_id: '',
        due_at: '',
        user_ids: []
    });
    const [assigning, setAssigning] = useState(false);

    const [uploadForm, setUploadForm] = useState({
        lesson_id: '',
        title: '',
        material_type: 'file',
        position: '',
        files: []
    });
    const [uploadingMaterials, setUploadingMaterials] = useState(false);

    const [revokeForm, setRevokeForm] = useState({
        certificate_id: '',
        reason: 'Revoked by administrator'
    });
    const [revoking, setRevoking] = useState(false);

    const tabVisibleRef = useRef(true);
    const playerPositionRef = useRef(0);
    const playerMaxObservedRef = useRef(0);
    const selectedLessonIdRef = useRef(null);
    const lessonMetaRef = useRef({
        durationSeconds: 0,
        allowFastForward: true,
        completionThreshold: COMPLETION_THRESHOLD_FALLBACK,
        heartbeatSeconds: HEARTBEAT_FALLBACK_SECONDS
    });
    const answersRef = useRef({});
    const testAttemptIdRef = useRef(null);

    useEffect(() => {
        tabVisibleRef.current = typeof document === 'undefined' ? true : document.visibilityState === 'visible';
    }, []);

    useEffect(() => {
        playerPositionRef.current = toNumber(player.positionSeconds, 0);
        playerMaxObservedRef.current = toNumber(player.maxObservedSeconds, 0);
    }, [player.positionSeconds, player.maxObservedSeconds]);

    useEffect(() => {
        selectedLessonIdRef.current = selectedLessonId;
    }, [selectedLessonId]);

    useEffect(() => {
        answersRef.current = answers;
    }, [answers]);

    useEffect(() => {
        testAttemptIdRef.current = testAttemptData?.attempt?.id || null;
    }, [testAttemptData]);

    useEffect(() => {
        if (!lessonData?.lesson) return;
        lessonMetaRef.current = {
            durationSeconds: toNumber(lessonData.lesson.duration_seconds, 0),
            allowFastForward: !!lessonData.lesson.allow_fast_forward,
            completionThreshold: toNumber(lessonData.lesson.completion_threshold, COMPLETION_THRESHOLD_FALLBACK),
            heartbeatSeconds: toNumber(
                lessonData?.anti_cheat?.heartbeat_seconds,
                toNumber(homeData?.heartbeat_seconds, HEARTBEAT_FALLBACK_SECONDS)
            )
        };
    }, [lessonData, homeData?.heartbeat_seconds]);

    const loadHome = useCallback(async (force = false) => {
        if (!isLearner) return;
        setHomeLoading(true);
        try {
            const payload = await request('GET', '/api/lms/home', { useCache: !force, cacheTtlMs: 10000 });
            if (!mountedRef.current) return;
            setHomeData(payload || null);
            setHomeLoaded(true);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось загрузить LMS dashboard');
            }
        } finally {
            if (mountedRef.current) setHomeLoading(false);
        }
    }, [isLearner, request, showErrorToast]);

    const loadCourses = useCallback(async (force = false) => {
        if (!isLearner) return;
        setCoursesLoading(true);
        try {
            const payload = await request('GET', '/api/lms/courses', { useCache: !force, cacheTtlMs: 10000 });
            if (!mountedRef.current) return;
            setCourses(Array.isArray(payload?.courses) ? payload.courses : []);
            setCoursesLoaded(true);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось загрузить курсы');
            }
        } finally {
            if (mountedRef.current) setCoursesLoading(false);
        }
    }, [isLearner, request, showErrorToast]);

    const loadCertificates = useCallback(async (force = false) => {
        if (!isLearner) return;
        setCertificatesLoading(true);
        try {
            const payload = await request('GET', '/api/lms/certificates', { useCache: !force, cacheTtlMs: 15000 });
            if (!mountedRef.current) return;
            setCertificates(Array.isArray(payload?.certificates) ? payload.certificates : []);
            setCertificatesLoaded(true);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось загрузить сертификаты');
            }
        } finally {
            if (mountedRef.current) setCertificatesLoading(false);
        }
    }, [isLearner, request, showErrorToast]);

    const loadNotifications = useCallback(async (force = false) => {
        if (!isLearner) return;
        setNotificationsLoading(true);
        try {
            const payload = await request('GET', '/api/lms/notifications', {
                params: { limit: 200 },
                useCache: !force,
                cacheTtlMs: 8000
            });
            if (!mountedRef.current) return;
            setNotifications(Array.isArray(payload?.notifications) ? payload.notifications : []);
            setNotificationsLoaded(true);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось загрузить уведомления');
            }
        } finally {
            if (mountedRef.current) setNotificationsLoading(false);
        }
    }, [isLearner, request, showErrorToast]);

    const loadAdminCourses = useCallback(async (force = false) => {
        if (!isManager) return;
        setAdminCoursesLoading(true);
        try {
            const payload = await request('GET', '/api/lms/admin/courses', { useCache: !force, cacheTtlMs: 12000 });
            if (!mountedRef.current) return;
            setAdminCourses(Array.isArray(payload?.courses) ? payload.courses : []);
            setAdminCoursesLoaded(true);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось загрузить админ-курсы');
            }
        } finally {
            if (mountedRef.current) setAdminCoursesLoading(false);
        }
    }, [isManager, request, showErrorToast]);

    const loadAdminProgress = useCallback(async (force = false) => {
        if (!isManager) return;
        setAdminProgressLoading(true);
        try {
            const payload = await request('GET', '/api/lms/admin/progress', { useCache: !force, cacheTtlMs: 12000 });
            if (!mountedRef.current) return;
            setAdminProgress(Array.isArray(payload?.rows) ? payload.rows : []);
            setAdminProgressLoaded(true);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось загрузить прогресс');
            }
        } finally {
            if (mountedRef.current) setAdminProgressLoading(false);
        }
    }, [isManager, request, showErrorToast]);

    const loadAdminAttempts = useCallback(async (force = false) => {
        if (!isManager) return;
        setAdminAttemptsLoading(true);
        try {
            const payload = await request('GET', '/api/lms/admin/attempts', { useCache: !force, cacheTtlMs: 10000 });
            if (!mountedRef.current) return;
            setAdminAttempts(Array.isArray(payload?.attempts) ? payload.attempts : []);
            setAdminAttemptsLoaded(true);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось загрузить попытки');
            }
        } finally {
            if (mountedRef.current) setAdminAttemptsLoading(false);
        }
    }, [isManager, request, showErrorToast]);

    const loadAssignableUsers = useCallback(async (force = false) => {
        if (!isManager) return;
        setAssignableUsersLoading(true);
        try {
            const payload = await request('GET', '/api/admin/users', { useCache: !force, cacheTtlMs: 60000 });
            if (!mountedRef.current) return;
            const usersList = Array.isArray(payload?.users) ? payload.users : [];
            setAssignableUsers(usersList.filter((item) => LEARNER_ROLES.has(String(item?.role || '').toLowerCase())));
            setAssignableUsersLoaded(true);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось загрузить список сотрудников');
            }
        } finally {
            if (mountedRef.current) setAssignableUsersLoading(false);
        }
    }, [isManager, request, showErrorToast]);

    const openCourse = useCallback(async (courseId, options = {}) => {
        if (!courseId) return;
        const { silentError = false } = options;
        setCourseDetailLoading(true);
        setCourseDetail(null);
        setLessonData(null);
        setSelectedLessonId(null);
        setPlayer((prev) => ({ ...prev, isPlaying: false, positionSeconds: 0, maxObservedSeconds: 0, seekDraft: null }));
        setTestAttemptData(null);
        setTestResult(null);
        setAnswers({});
        setAnswerSavingState({});
        answerDirtyRef.current.clear();
        answerLastSavedRef.current.clear();
        answerTimersRef.current.forEach((timerId) => window.clearTimeout(timerId));
        answerTimersRef.current.clear();

        try {
            const payload = await runExclusive('course-detail', (signal) => request('GET', `/api/lms/courses/${courseId}`, {
                signal,
                useCache: false,
                dedupe: false
            }));
            if (!mountedRef.current) return;
            setCourseDetail(payload?.course || null);
        } catch (error) {
            if (!isAbortError(error) && !silentError) {
                showErrorToast(error?.response?.data?.error || 'Не удалось открыть курс');
            }
        } finally {
            if (mountedRef.current) setCourseDetailLoading(false);
        }
    }, [request, runExclusive, showErrorToast]);

    const sendLessonEvent = useCallback(async (eventType, payload = {}) => {
        const lessonId = selectedLessonIdRef.current;
        if (!lessonId) return { status: 'skipped' };
        try {
            return await request('POST', `/api/lms/lessons/${lessonId}/event`, {
                data: {
                    event_type: eventType,
                    payload,
                    client_ts: new Date().toISOString()
                },
                dedupe: false
            });
        } catch (error) {
            if (error?.response?.status === 409 && error?.response?.data?.reason === 'FORWARD_SEEK_NOT_ALLOWED') {
                const allowed = toNumber(error?.response?.data?.allowed_position, 0);
                setPlayer((prev) => ({
                    ...prev,
                    isPlaying: false,
                    positionSeconds: clamp(allowed, 0, Math.max(allowed, lessonMetaRef.current.durationSeconds))
                }));
                showToast?.('Перемотка вперед заблокирована сервером anti-cheat.', 'info');
                return error.response.data;
            }
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось отправить событие урока');
            }
            throw error;
        }
    }, [request, showErrorToast, showToast]);

    const openLesson = useCallback(async (lessonId) => {
        if (!lessonId) return;
        setLessonLoading(true);
        setSelectedLessonId(lessonId);
        setPlayer((prev) => ({ ...prev, isPlaying: false, seekDraft: null }));
        setTestAttemptData(null);
        setTestResult(null);
        try {
            const payload = await runExclusive('lesson-detail', (signal) => request('GET', `/api/lms/lessons/${lessonId}`, {
                signal,
                useCache: false,
                dedupe: false
            }));
            if (!mountedRef.current) return;
            setLessonData(payload || null);
            const initialPosition = toNumber(payload?.progress?.max_position_seconds, 0);
            const confirmed = toNumber(payload?.progress?.confirmed_seconds, initialPosition);
            setPlayer((prev) => ({
                ...prev,
                isPlaying: false,
                positionSeconds: initialPosition,
                maxObservedSeconds: Math.max(initialPosition, confirmed),
                seekDraft: null
            }));
            await sendLessonEvent('open', { lesson_id: lessonId });
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось открыть урок');
            }
        } finally {
            if (mountedRef.current) setLessonLoading(false);
        }
    }, [request, runExclusive, sendLessonEvent, showErrorToast]);

    const syncHeartbeat = useCallback(async () => {
        const lessonId = selectedLessonIdRef.current;
        if (!lessonId || heartbeatBusyRef.current) return;
        heartbeatBusyRef.current = true;
        try {
            const payload = await request('POST', `/api/lms/lessons/${lessonId}/heartbeat`, {
                data: {
                    position_seconds: playerPositionRef.current,
                    tab_visible: tabVisibleRef.current,
                    client_ts: new Date().toISOString()
                },
                dedupe: false
            });
            if (!mountedRef.current) return;

            const serverPosition = toNumber(payload?.position_seconds, playerPositionRef.current);
            const completionRatio = toNumber(payload?.completion_ratio, toNumber(lessonData?.progress?.completion_ratio, 0));
            setPlayer((prev) => ({
                ...prev,
                positionSeconds: serverPosition,
                maxObservedSeconds: Math.max(prev.maxObservedSeconds, serverPosition)
            }));
            setLessonData((prev) => {
                if (!prev) return prev;
                const nextProgress = {
                    ...(prev.progress || {}),
                    max_position_seconds: serverPosition,
                    confirmed_seconds: Math.max(
                        toNumber(prev.progress?.confirmed_seconds, 0),
                        serverPosition
                    ),
                    completion_ratio: completionRatio,
                    active_seconds: toNumber(payload?.active_seconds, prev.progress?.active_seconds || 0),
                    stale_gap_count: toNumber(prev.progress?.stale_gap_count, 0) + (payload?.stale_gap ? 1 : 0),
                    can_complete: completionRatio >= toNumber(prev.lesson?.completion_threshold, COMPLETION_THRESHOLD_FALLBACK)
                };
                return { ...prev, progress: nextProgress };
            });

            if (payload?.blocked_forward_seek && payload?.allowed_position !== undefined && payload?.allowed_position !== null) {
                const allowed = toNumber(payload.allowed_position, serverPosition);
                setPlayer((prev) => ({
                    ...prev,
                    isPlaying: false,
                    positionSeconds: allowed,
                    maxObservedSeconds: Math.max(prev.maxObservedSeconds, allowed)
                }));
                showToast?.('Прогресс скорректирован сервером anti-cheat.', 'info');
            }
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Heartbeat не отправлен');
            }
        } finally {
            heartbeatBusyRef.current = false;
        }
    }, [lessonData?.progress?.completion_ratio, request, showErrorToast, showToast]);

    const seekTo = useCallback(async (nextPosition, force = false) => {
        const duration = lessonMetaRef.current.durationSeconds;
        const bounded = clamp(
            toNumber(nextPosition, 0),
            0,
            duration > 0 ? duration : toNumber(nextPosition, 0)
        );
        const current = playerPositionRef.current;
        if (!force && Math.abs(current - bounded) < 0.2) return;

        try {
            await sendLessonEvent('seek', {
                from_seconds: current,
                to_seconds: bounded
            });
            if (!mountedRef.current) return;
            setPlayer((prev) => ({
                ...prev,
                positionSeconds: bounded,
                maxObservedSeconds: Math.max(prev.maxObservedSeconds, bounded),
                seekDraft: null
            }));
        } catch (error) {
            if (!isAbortError(error)) {
                // toast is already shown in sendLessonEvent
            }
        }
    }, [sendLessonEvent]);

    const completeLesson = useCallback(async () => {
        const lessonId = selectedLessonIdRef.current;
        if (!lessonId) return;
        try {
            await syncHeartbeat();
            await request('POST', `/api/lms/lessons/${lessonId}/complete`, {
                data: {},
                dedupe: false
            });
            showToast?.('Урок успешно завершен.', 'success');
            invalidateLmsCache();
            await Promise.all([
                loadHome(true),
                loadCourses(true),
                selectedCourseId ? openCourse(selectedCourseId, { silentError: true }) : Promise.resolve()
            ]);
            await openLesson(lessonId);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось завершить урок');
            }
        }
    }, [invalidateLmsCache, loadCourses, loadHome, openCourse, openLesson, request, selectedCourseId, showErrorToast, showToast, syncHeartbeat]);

    const saveAnswerNow = useCallback(async (questionId, payload) => {
        const attemptId = testAttemptIdRef.current;
        if (!attemptId) return;
        const normalizedQuestionId = toNumber(questionId, 0);
        if (!normalizedQuestionId) return;
        const serialized = safeJson(payload);
        if (answerLastSavedRef.current.get(normalizedQuestionId) === serialized) {
            answerDirtyRef.current.delete(normalizedQuestionId);
            return;
        }

        setAnswerSavingState((prev) => ({ ...prev, [normalizedQuestionId]: 'saving' }));
        try {
            await request('PATCH', `/api/lms/tests/attempts/${attemptId}/answer`, {
                data: {
                    question_id: normalizedQuestionId,
                    answer_payload: payload
                },
                dedupe: false
            });
            answerLastSavedRef.current.set(normalizedQuestionId, serialized);
            answerDirtyRef.current.delete(normalizedQuestionId);
            setAnswerSavingState((prev) => ({ ...prev, [normalizedQuestionId]: 'saved' }));
        } catch (error) {
            if (!isAbortError(error)) {
                setAnswerSavingState((prev) => ({ ...prev, [normalizedQuestionId]: 'error' }));
                showErrorToast(error?.response?.data?.error || 'Не удалось сохранить ответ');
            }
        }
    }, [request, showErrorToast]);

    const queueAnswerSave = useCallback((questionId, payload, immediate = false) => {
        const normalizedQuestionId = toNumber(questionId, 0);
        if (!normalizedQuestionId) return;
        setAnswers((prev) => ({ ...prev, [normalizedQuestionId]: payload }));
        answerDirtyRef.current.add(normalizedQuestionId);

        const existing = answerTimersRef.current.get(normalizedQuestionId);
        if (existing) {
            window.clearTimeout(existing);
            answerTimersRef.current.delete(normalizedQuestionId);
        }

        if (immediate) {
            void saveAnswerNow(normalizedQuestionId, payload);
            return;
        }

        const timerId = window.setTimeout(() => {
            answerTimersRef.current.delete(normalizedQuestionId);
            const latestPayload = answersRef.current[normalizedQuestionId] ?? payload;
            void saveAnswerNow(normalizedQuestionId, latestPayload);
        }, 650);
        answerTimersRef.current.set(normalizedQuestionId, timerId);
    }, [saveAnswerNow]);

    const flushAnswerQueue = useCallback(async () => {
        const pendingPromises = [];

        for (const [questionId, timerId] of answerTimersRef.current.entries()) {
            window.clearTimeout(timerId);
            answerTimersRef.current.delete(questionId);
            const latestPayload = answersRef.current[questionId];
            pendingPromises.push(saveAnswerNow(questionId, latestPayload ?? {}));
        }

        for (const questionId of Array.from(answerDirtyRef.current)) {
            if (!answerTimersRef.current.has(questionId)) {
                const latestPayload = answersRef.current[questionId];
                pendingPromises.push(saveAnswerNow(questionId, latestPayload ?? {}));
            }
        }

        if (pendingPromises.length > 0) {
            await Promise.allSettled(pendingPromises);
        }
    }, [saveAnswerNow]);

    const startCourse = useCallback(async (courseId) => {
        const id = toNumber(courseId, 0);
        if (!id) return;
        try {
            await request('POST', `/api/lms/courses/${id}/start`, {
                data: {},
                dedupe: false
            });
            invalidateLmsCache();
            await Promise.all([loadHome(true), loadCourses(true)]);
            showToast?.('Курс запущен.', 'success');
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось запустить курс');
            }
        }
    }, [invalidateLmsCache, loadCourses, loadHome, request, showErrorToast, showToast]);

    const startTest = useCallback(async (testId) => {
        const id = toNumber(testId, 0);
        if (!id) return;
        setTestLoading(true);
        setTestResult(null);
        setLessonData((prev) => (prev ? { ...prev } : prev));
        setPlayer((prev) => ({ ...prev, isPlaying: false }));
        answerTimersRef.current.forEach((timerId) => window.clearTimeout(timerId));
        answerTimersRef.current.clear();
        answerDirtyRef.current.clear();
        answerLastSavedRef.current.clear();
        setAnswers({});
        setAnswerSavingState({});

        try {
            const payload = await runExclusive('test-start', (signal) => request('POST', `/api/lms/tests/${id}/start`, {
                signal,
                data: {},
                dedupe: false
            }));
            if (!mountedRef.current) return;
            setTestAttemptData(payload || null);
            setTestResult(null);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось начать тест');
            }
        } finally {
            if (mountedRef.current) setTestLoading(false);
        }
    }, [request, runExclusive, showErrorToast]);

    const finishTest = useCallback(async () => {
        const attemptId = testAttemptIdRef.current;
        if (!attemptId) return;
        setFinishingTest(true);
        try {
            await flushAnswerQueue();
            const finishPayload = await request('POST', `/api/lms/tests/attempts/${attemptId}/finish`, {
                data: {},
                dedupe: false
            });
            const resultPayload = await request('GET', `/api/lms/tests/attempts/${attemptId}/result`, {
                useCache: false,
                dedupe: false
            });
            if (!mountedRef.current) return;
            setTestResult({
                summary: finishPayload?.result || null,
                detail: resultPayload || null
            });
            invalidateLmsCache();
            await Promise.all([
                loadHome(true),
                loadCourses(true),
                loadCertificates(true),
                selectedCourseId ? openCourse(selectedCourseId, { silentError: true }) : Promise.resolve()
            ]);
            showToast?.('Тест завершен.', 'success');
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось завершить тест');
            }
        } finally {
            if (mountedRef.current) setFinishingTest(false);
        }
    }, [flushAnswerQueue, invalidateLmsCache, loadCertificates, loadCourses, loadHome, openCourse, request, selectedCourseId, showErrorToast, showToast]);

    const markNotificationRead = useCallback(async (notificationId) => {
        const id = toNumber(notificationId, 0);
        if (!id) return;
        setNotifications((prev) => prev.map((item) => (
            item.id === id ? { ...item, is_read: true, read_at: item.read_at || new Date().toISOString() } : item
        )));
        try {
            await request('POST', `/api/lms/notifications/${id}/read`, {
                data: {},
                dedupe: false
            });
            invalidateLmsCache();
            await loadHome(true);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось отметить уведомление как прочитанное');
                await loadNotifications(true);
            }
        }
    }, [invalidateLmsCache, loadHome, loadNotifications, request, showErrorToast]);

    const downloadCertificate = useCallback(async (certificateId, certificateNumber) => {
        const id = toNumber(certificateId, 0);
        if (!id) return;
        setDownloadingCertificateId(id);
        try {
            const response = await request('GET', `/api/lms/certificates/${id}/download`, {
                responseType: 'blob',
                dedupe: false
            });
            const blob = response?.data instanceof Blob
                ? response.data
                : new Blob([response?.data], { type: 'application/pdf' });
            const objectUrl = window.URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = objectUrl;
            anchor.download = `${certificateNumber || `certificate-${id}`}.pdf`;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            window.URL.revokeObjectURL(objectUrl);
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось скачать сертификат');
            }
        } finally {
            if (mountedRef.current) setDownloadingCertificateId(0);
        }
    }, [request, showErrorToast]);

    const createCourse = useCallback(async () => {
        const title = String(newCourseForm.title || '').trim();
        if (!title) {
            showToast?.('Введите название курса.', 'info');
            return;
        }
        let modules = [];
        let tests = [];
        if (String(newCourseForm.blueprint_json || '').trim()) {
            try {
                const parsed = JSON.parse(newCourseForm.blueprint_json);
                modules = Array.isArray(parsed?.modules) ? parsed.modules : [];
                tests = Array.isArray(parsed?.tests) ? parsed.tests : [];
            } catch {
                showToast?.('Blueprint JSON содержит ошибку.', 'error');
                return;
            }
        }

        setCreateCourseLoading(true);
        try {
            await request('POST', '/api/lms/admin/courses', {
                data: {
                    title,
                    description: newCourseForm.description || '',
                    category: newCourseForm.category || '',
                    pass_threshold: toNumber(newCourseForm.pass_threshold, 80),
                    attempt_limit: toNumber(newCourseForm.attempt_limit, 3),
                    modules,
                    tests
                },
                dedupe: false
            });
            invalidateLmsCache();
            setNewCourseForm({
                title: '',
                description: '',
                category: '',
                pass_threshold: 80,
                attempt_limit: 3,
                blueprint_json: ''
            });
            await loadAdminCourses(true);
            showToast?.('Курс создан.', 'success');
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось создать курс');
            }
        } finally {
            if (mountedRef.current) setCreateCourseLoading(false);
        }
    }, [invalidateLmsCache, loadAdminCourses, newCourseForm, request, showErrorToast, showToast]);

    const publishCourse = useCallback(async (courseId, versionId) => {
        const id = toNumber(courseId, 0);
        if (!id) return;
        try {
            await request('POST', `/api/lms/admin/courses/${id}/publish`, {
                data: versionId ? { course_version_id: versionId } : {},
                dedupe: false
            });
            invalidateLmsCache();
            await loadAdminCourses(true);
            showToast?.('Версия курса опубликована.', 'success');
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось опубликовать курс');
            }
        }
    }, [invalidateLmsCache, loadAdminCourses, request, showErrorToast, showToast]);

    const assignCourse = useCallback(async () => {
        const courseId = toNumber(assignmentForm.course_id, 0);
        if (!courseId) {
            showToast?.('Выберите курс для назначения.', 'info');
            return;
        }
        if (!Array.isArray(assignmentForm.user_ids) || assignmentForm.user_ids.length === 0) {
            showToast?.('Выберите хотя бы одного сотрудника.', 'info');
            return;
        }
        setAssigning(true);
        try {
            await request('POST', `/api/lms/admin/courses/${courseId}/assignments`, {
                data: {
                    user_ids: assignmentForm.user_ids,
                    due_at: assignmentForm.due_at || undefined
                },
                dedupe: false
            });
            invalidateLmsCache();
            await Promise.all([loadAdminProgress(true), loadAdminCourses(true)]);
            showToast?.('Курс назначен сотрудникам.', 'success');
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось назначить курс');
            }
        } finally {
            if (mountedRef.current) setAssigning(false);
        }
    }, [assignmentForm, invalidateLmsCache, loadAdminCourses, loadAdminProgress, request, showErrorToast, showToast]);

    const uploadMaterials = useCallback(async () => {
        if (!uploadForm.files || uploadForm.files.length === 0) {
            showToast?.('Добавьте файлы для загрузки.', 'info');
            return;
        }
        const formData = new FormData();
        uploadForm.files.forEach((file) => formData.append('files', file));
        if (uploadForm.lesson_id) formData.append('lesson_id', uploadForm.lesson_id);
        if (uploadForm.title) formData.append('title', uploadForm.title);
        if (uploadForm.material_type) formData.append('material_type', uploadForm.material_type);
        if (uploadForm.position) formData.append('position', uploadForm.position);

        setUploadingMaterials(true);
        try {
            await request('POST', '/api/lms/admin/materials/upload', {
                data: formData,
                dedupe: false
            });
            invalidateLmsCache();
            setUploadForm({
                lesson_id: '',
                title: '',
                material_type: 'file',
                position: '',
                files: []
            });
            showToast?.('Материалы успешно загружены.', 'success');
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось загрузить материалы');
            }
        } finally {
            if (mountedRef.current) setUploadingMaterials(false);
        }
    }, [invalidateLmsCache, request, showErrorToast, showToast, uploadForm]);

    const revokeCertificate = useCallback(async () => {
        const certificateId = toNumber(revokeForm.certificate_id, 0);
        if (!certificateId) {
            showToast?.('Введите ID сертификата.', 'info');
            return;
        }
        setRevoking(true);
        try {
            await request('POST', `/api/lms/admin/certificates/${certificateId}/revoke`, {
                data: { reason: revokeForm.reason || 'Revoked by administrator' },
                dedupe: false
            });
            setRevokeForm({ certificate_id: '', reason: 'Revoked by administrator' });
            showToast?.('Сертификат отозван.', 'success');
        } catch (error) {
            if (!isAbortError(error)) {
                showErrorToast(error?.response?.data?.error || 'Не удалось отозвать сертификат');
            }
        } finally {
            if (mountedRef.current) setRevoking(false);
        }
    }, [request, revokeForm, showErrorToast, showToast]);

    useEffect(() => {
        if (!isLearner) return;
        void loadHome(false);
        void loadCourses(false);
    }, [isLearner, loadHome, loadCourses]);

    useEffect(() => {
        if (!isManager) return;
        void loadAdminCourses(false);
    }, [isManager, loadAdminCourses]);

    useEffect(() => {
        if (!isLearner) return;
        if (activeTab === 'certificates' && !certificatesLoaded && !certificatesLoading) {
            void loadCertificates(false);
        }
        if (activeTab === 'notifications' && !notificationsLoaded && !notificationsLoading) {
            void loadNotifications(false);
        }
    }, [
        activeTab,
        certificatesLoaded,
        certificatesLoading,
        isLearner,
        loadCertificates,
        loadNotifications,
        notificationsLoaded,
        notificationsLoading
    ]);

    useEffect(() => {
        if (!isManager) return;
        if (activeTab === 'admin_progress' && !adminProgressLoaded && !adminProgressLoading) {
            void loadAdminProgress(false);
        }
        if (activeTab === 'admin_attempts' && !adminAttemptsLoaded && !adminAttemptsLoading) {
            void loadAdminAttempts(false);
        }
        if (activeTab === 'admin_deadlines' && !adminProgressLoaded && !adminProgressLoading) {
            void loadAdminProgress(false);
        }
        if (activeTab === 'admin_assignments' && !assignableUsersLoaded && !assignableUsersLoading) {
            void loadAssignableUsers(false);
        }
    }, [
        activeTab,
        adminAttemptsLoaded,
        adminAttemptsLoading,
        adminProgressLoaded,
        adminProgressLoading,
        assignableUsersLoaded,
        assignableUsersLoading,
        isManager,
        loadAdminAttempts,
        loadAdminProgress,
        loadAssignableUsers
    ]);

    useEffect(() => {
        if (!isLearner) return;
        if (selectedCourseId) return;
        if (!courses.length) return;
        const firstId = toNumber(courses[0]?.course_id, 0);
        if (!firstId) return;
        setSelectedCourseId(firstId);
        void openCourse(firstId, { silentError: true });
    }, [courses, isLearner, openCourse, selectedCourseId]);

    useEffect(() => {
        if (!selectedLessonId || !lessonData?.lesson?.id) return undefined;
        const intervalSeconds = Math.max(5, toNumber(lessonMetaRef.current.heartbeatSeconds, HEARTBEAT_FALLBACK_SECONDS));
        const timerId = window.setInterval(() => {
            void syncHeartbeat();
        }, intervalSeconds * 1000);
        return () => window.clearInterval(timerId);
    }, [lessonData?.lesson?.id, selectedLessonId, syncHeartbeat]);

    useEffect(() => {
        if (!lessonData?.lesson?.id || !player.isPlaying) return undefined;
        const tick = window.setInterval(() => {
            setPlayer((prev) => {
                const duration = lessonMetaRef.current.durationSeconds;
                if (duration > 0 && prev.positionSeconds >= duration) {
                    return { ...prev, isPlaying: false, positionSeconds: duration, seekDraft: null };
                }
                const next = prev.positionSeconds + 1;
                const bounded = duration > 0 ? Math.min(next, duration) : next;
                return {
                    ...prev,
                    positionSeconds: bounded,
                    maxObservedSeconds: Math.max(prev.maxObservedSeconds, bounded),
                    seekDraft: null
                };
            });
        }, 1000);
        return () => window.clearInterval(tick);
    }, [lessonData?.lesson?.id, player.isPlaying]);

    useEffect(() => {
        if (!selectedLessonId) return undefined;
        const handler = () => {
            const visible = document.visibilityState === 'visible';
            tabVisibleRef.current = visible;
            if (!visible) {
                setPlayer((prev) => ({ ...prev, isPlaying: false }));
            }
            void sendLessonEvent('visibility', { is_visible: visible });
        };
        document.addEventListener('visibilitychange', handler);
        return () => {
            document.removeEventListener('visibilitychange', handler);
        };
    }, [selectedLessonId, sendLessonEvent]);

    const refreshActiveTab = useCallback(async () => {
        switch (activeTab) {
        case 'dashboard':
            await Promise.all([loadHome(true), loadCourses(true)]);
            break;
        case 'courses':
            await Promise.all([
                loadHome(true),
                loadCourses(true),
                selectedCourseId ? openCourse(selectedCourseId, { silentError: true }) : Promise.resolve()
            ]);
            break;
        case 'certificates':
            await loadCertificates(true);
            break;
        case 'notifications':
            await loadNotifications(true);
            break;
        case 'admin_courses':
            await loadAdminCourses(true);
            break;
        case 'admin_assignments':
            await Promise.all([loadAdminCourses(true), loadAssignableUsers(true)]);
            break;
        case 'admin_progress':
        case 'admin_deadlines':
            await loadAdminProgress(true);
            break;
        case 'admin_attempts':
            await loadAdminAttempts(true);
            break;
        case 'admin_materials':
            await loadAdminCourses(true);
            break;
        default:
            break;
        }
    }, [
        activeTab,
        loadAdminAttempts,
        loadAdminCourses,
        loadAdminProgress,
        loadAssignableUsers,
        loadCertificates,
        loadCourses,
        loadHome,
        loadNotifications,
        openCourse,
        selectedCourseId
    ]);

    const homeCourses = useMemo(() => Array.isArray(homeData?.courses) ? homeData.courses : [], [homeData?.courses]);
    const homeByCourseId = useMemo(() => {
        const map = new Map();
        homeCourses.forEach((item) => {
            map.set(toNumber(item?.course_id, 0), item);
        });
        return map;
    }, [homeCourses]);

    const mergedCourses = useMemo(() => (
        courses.map((item) => {
            const courseId = toNumber(item?.course_id, 0);
            const homeItem = homeByCourseId.get(courseId) || {};
            return {
                ...item,
                progress_percent: toNumber(homeItem.progress_percent, 0),
                completed_lessons: toNumber(homeItem.completed_lessons, 0),
                total_lessons: toNumber(homeItem.total_lessons, 0),
                best_score: homeItem.best_score ?? null,
                deadline_status: homeItem.deadline_status || item.deadline_status || 'unknown',
                assignment_status: homeItem.status || item.status
            };
        })
    ), [courses, homeByCourseId]);

    const filteredCourses = useMemo(() => {
        const query = String(courseSearch || '').trim().toLowerCase();
        if (!query) return mergedCourses;
        return mergedCourses.filter((item) => {
            const haystack = `${item?.title || ''} ${item?.description || ''} ${item?.category || ''}`.toLowerCase();
            return haystack.includes(query);
        });
    }, [courseSearch, mergedCourses]);

    const visibleUsers = useMemo(() => {
        const query = String(userSearch || '').trim().toLowerCase();
        if (!query) return assignableUsers;
        return assignableUsers.filter((item) => {
            const haystack = `${item?.name || ''} ${item?.login || ''} ${item?.role || ''}`.toLowerCase();
            return haystack.includes(query);
        });
    }, [assignableUsers, userSearch]);

    const unreadNotifications = useMemo(
        () => notifications.filter((item) => !item?.is_read).length,
        [notifications]
    );

    const learnerMetrics = useMemo(() => {
        const assigned = homeCourses.length;
        const completed = homeCourses.filter((item) => item.status === 'completed').length;
        const inProgress = homeCourses.filter((item) => item.status === 'in_progress').length;
        const overdue = homeCourses.filter((item) => item.deadline_status === 'overdue').length;
        return { assigned, completed, inProgress, overdue };
    }, [homeCourses]);

    const lessonProgress = lessonData?.progress || null;
    const lessonCompletionRatio = toNumber(lessonProgress?.completion_ratio, 0);
    const lessonThreshold = toNumber(lessonData?.lesson?.completion_threshold, COMPLETION_THRESHOLD_FALLBACK);
    const lessonCanComplete = lessonCompletionRatio >= lessonThreshold || !!lessonProgress?.can_complete;
    const heartbeatSeconds = toNumber(lessonData?.anti_cheat?.heartbeat_seconds, toNumber(homeData?.heartbeat_seconds, HEARTBEAT_FALLBACK_SECONDS));
    const staleGapSeconds = toNumber(lessonData?.anti_cheat?.stale_gap_seconds, STALE_GAP_FALLBACK_SECONDS);

    const selectedCourseTests = useMemo(() => (
        Array.isArray(courseDetail?.tests) ? courseDetail.tests : []
    ), [courseDetail?.tests]);

    const selectedCourseModules = useMemo(() => (
        Array.isArray(courseDetail?.modules) ? courseDetail.modules : []
    ), [courseDetail?.modules]);

    const selectedCourseAssignment = courseDetail?.assignment || {};

    const progressRowsSorted = useMemo(() => (
        [...adminProgress].sort((a, b) => {
            const da = a?.due_at ? new Date(a.due_at).getTime() : Number.MAX_SAFE_INTEGER;
            const db = b?.due_at ? new Date(b.due_at).getTime() : Number.MAX_SAFE_INTEGER;
            return da - db;
        })
    ), [adminProgress]);

    const deadlinesRows = useMemo(() => (
        progressRowsSorted.filter((row) => ['on_time', 'late_completed', 'overdue'].includes(row?.deadline_status))
    ), [progressRowsSorted]);

    const currentAttemptId = testAttemptData?.attempt?.id || null;
    const currentAttemptQuestions = Array.isArray(testAttemptData?.questions) ? testAttemptData.questions : [];

    return (
        <div className="space-y-5" style={{ fontFamily: '"Manrope", "Segoe UI", sans-serif' }}>
            <section className="relative overflow-hidden rounded-3xl border border-slate-200 bg-gradient-to-br from-slate-950 via-slate-900 to-blue-900 p-6 text-white shadow-xl">
                <div
                    className="pointer-events-none absolute inset-0 opacity-60"
                    style={{
                        backgroundImage:
                            'radial-gradient(circle at 18% 18%, rgba(56, 189, 248, 0.35), transparent 42%), radial-gradient(circle at 85% 70%, rgba(34, 211, 238, 0.22), transparent 45%), radial-gradient(circle at 60% 5%, rgba(59, 130, 246, 0.2), transparent 35%)'
                    }}
                />
                <div className="relative z-10 flex flex-wrap items-start justify-between gap-4">
                    <div className="space-y-2">
                        <div className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.15em] text-cyan-200">
                            <ShieldCheck className="h-4 w-4" />
                            LMS OTP
                        </div>
                        <h2 className="text-2xl font-semibold">Раздел обучения</h2>
                        <p className="max-w-2xl text-sm text-slate-200">
                            Корпоративный LMS с серверным anti-cheat: heartbeat, контроль вкладки, блокировка перемотки и
                            серверная валидация прохождения.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={() => { void refreshActiveTab(); }}
                        className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-4 py-2 text-sm font-medium text-white transition hover:bg-white/20"
                    >
                        <RefreshCw className="h-4 w-4" />
                        Обновить
                    </button>
                </div>
                {isLearner && (
                    <div className="relative z-10 mt-5 grid grid-cols-1 gap-3 md:grid-cols-4">
                        <MetricCard icon={BookOpen} label="Назначено" value={learnerMetrics.assigned} helper="Активные назначения" />
                        <MetricCard icon={CheckCircle2} label="Завершено" value={learnerMetrics.completed} helper="Курсы завершены" />
                        <MetricCard icon={Clock3} label="В процессе" value={learnerMetrics.inProgress} helper="Текущая нагрузка" />
                        <MetricCard icon={AlertTriangle} label="Просрочка" value={learnerMetrics.overdue} helper="Требует внимания" />
                    </div>
                )}
            </section>

            <div className="rounded-2xl border border-slate-200 bg-white p-2 shadow-sm">
                <div className="flex flex-wrap gap-2">
                    {availableTabs.map((tab) => (
                        <TabButton
                            key={tab.id}
                            icon={tab.icon}
                            label={tab.label}
                            active={tab.id === activeTab}
                            onClick={() => setActiveTab(tab.id)}
                        />
                    ))}
                </div>
            </div>

            {isLearner && activeTab === 'dashboard' && (
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.2fr_0.8fr]">
                    <SectionCard
                        title="Курсы с ближайшими дедлайнами"
                        subtitle="Зелёный: в срок, оранжевый: с опозданием, красный: просрочено"
                    >
                        {homeLoading && !homeLoaded ? (
                            <div className="flex items-center gap-2 text-sm text-slate-500">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Загрузка...
                            </div>
                        ) : homeCourses.length === 0 ? (
                            <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                                Пока нет назначенных курсов.
                            </div>
                        ) : (
                            <div className="space-y-3">
                                {homeCourses.map((item) => {
                                    const meta = deadlineMetaFor(item.deadline_status);
                                    return (
                                        <div key={item.assignment_id} className="rounded-xl border border-slate-200 p-3">
                                            <div className="flex flex-wrap items-center justify-between gap-2">
                                                <div>
                                                    <div className="text-sm font-semibold text-slate-900">{item.title}</div>
                                                    <div className="mt-1 text-xs text-slate-500">
                                                        Статус: {statusLabel(item.status)} · Дедлайн: {formatDateTime(item.due_at)}
                                                    </div>
                                                </div>
                                                <span className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ${meta.badge}`}>
                                                    {meta.label}
                                                </span>
                                            </div>
                                            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                                                <div
                                                    className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-600"
                                                    style={{ width: `${clamp(toNumber(item.progress_percent, 0), 0, 100)}%` }}
                                                />
                                            </div>
                                            <div className="mt-2 text-xs text-slate-500">
                                                Прогресс: {formatPercent(item.progress_percent)} · {toNumber(item.completed_lessons, 0)} / {toNumber(item.total_lessons, 0)} уроков
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </SectionCard>

                    <SectionCard title="Сводка anti-cheat" subtitle="Параметры применяются сервером">
                        <div className="space-y-3">
                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                                    <Timer className="h-4 w-4 text-slate-500" />
                                    Heartbeat
                                </div>
                                <div className="mt-1 text-xs text-slate-500">{toNumber(homeData?.heartbeat_seconds, HEARTBEAT_FALLBACK_SECONDS)} секунд</div>
                            </div>
                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                                    <TimerReset className="h-4 w-4 text-slate-500" />
                                    Stale gap
                                </div>
                                <div className="mt-1 text-xs text-slate-500">{toNumber(homeData?.stale_gap_seconds, STALE_GAP_FALLBACK_SECONDS)} секунд</div>
                            </div>
                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                                    <CheckCheck className="h-4 w-4 text-slate-500" />
                                    Порог завершения урока
                                </div>
                                <div className="mt-1 text-xs text-slate-500">{toNumber(homeData?.completion_threshold_percent, COMPLETION_THRESHOLD_FALLBACK)}%</div>
                            </div>
                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                                    <Bell className="h-4 w-4 text-slate-500" />
                                    Непрочитанные уведомления
                                </div>
                                <div className="mt-1 text-xs text-slate-500">{toNumber(homeData?.unread_notifications, unreadNotifications)} шт.</div>
                            </div>
                        </div>
                    </SectionCard>
                </div>
            )}

            {isLearner && activeTab === 'courses' && (
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-[340px_1fr]">
                    <SectionCard
                        title="Витрина курсов"
                        subtitle="Открывайте курс, изучайте модули и проходите тесты"
                    >
                        <label className="mb-3 block">
                            <span className="mb-1 inline-flex items-center gap-1 text-xs font-medium text-slate-500">
                                <Search className="h-3 w-3" /> Поиск
                            </span>
                            <input
                                value={courseSearch}
                                onChange={(event) => setCourseSearch(event.target.value)}
                                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                                placeholder="Название, категория..."
                            />
                        </label>
                        {coursesLoading && !coursesLoaded ? (
                            <div className="flex items-center gap-2 text-sm text-slate-500">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Загрузка курсов...
                            </div>
                        ) : filteredCourses.length === 0 ? (
                            <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                                Курсы не найдены.
                            </div>
                        ) : (
                            <div className="space-y-2">
                                {filteredCourses.map((item) => {
                                    const courseId = toNumber(item.course_id, 0);
                                    const selected = selectedCourseId === courseId;
                                    const deadlineMeta = deadlineMetaFor(item.deadline_status);
                                    return (
                                        <button
                                            key={item.assignment_id || item.course_id}
                                            type="button"
                                            onClick={() => {
                                                setSelectedCourseId(courseId);
                                                void openCourse(courseId);
                                            }}
                                            className={`w-full rounded-xl border p-3 text-left transition ${
                                                selected
                                                    ? 'border-blue-500 bg-blue-50/80'
                                                    : 'border-slate-200 bg-white hover:border-slate-300'
                                            }`}
                                        >
                                            <div className="text-sm font-semibold text-slate-900">{item.title}</div>
                                            <div className="mt-1 text-xs text-slate-500 line-clamp-2">{item.description || 'Без описания'}</div>
                                            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                                                <span className={`rounded-full px-2 py-1 ${deadlineMeta.badge}`}>{deadlineMeta.label}</span>
                                                <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">
                                                    {statusLabel(item.assignment_status)}
                                                </span>
                                            </div>
                                            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200">
                                                <div
                                                    className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-600"
                                                    style={{ width: `${clamp(toNumber(item.progress_percent, 0), 0, 100)}%` }}
                                                />
                                            </div>
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </SectionCard>

                    <div className="space-y-4">
                        <SectionCard
                            title={courseDetail?.title || 'Курс не выбран'}
                            subtitle={courseDetail?.description || 'Выберите курс слева'}
                            actions={selectedCourseId ? (
                                <button
                                    type="button"
                                    onClick={() => { void startCourse(selectedCourseId); }}
                                    className="rounded-xl bg-blue-700 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-blue-800"
                                >
                                    Старт курса
                                </button>
                            ) : null}
                        >
                            {courseDetailLoading ? (
                                <div className="flex items-center gap-2 text-sm text-slate-500">
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Загрузка структуры курса...
                                </div>
                            ) : !courseDetail ? (
                                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                                    Откройте курс, чтобы увидеть уроки и тесты.
                                </div>
                            ) : (
                                <div className="space-y-4">
                                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                            <div className="text-xs text-slate-500">Статус</div>
                                            <div className="mt-1 text-sm font-semibold text-slate-800">
                                                {statusLabel(selectedCourseAssignment?.status)}
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                            <div className="text-xs text-slate-500">Дедлайн</div>
                                            <div className="mt-1 text-sm font-semibold text-slate-800">
                                                {formatDateTime(selectedCourseAssignment?.due_at)}
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                            <div className="text-xs text-slate-500">Цвет дедлайна</div>
                                            <div className="mt-1">
                                                <span className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${deadlineMetaFor(selectedCourseAssignment?.deadline_status).badge}`}>
                                                    {deadlineMetaFor(selectedCourseAssignment?.deadline_status).label}
                                                </span>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="space-y-3">
                                        {selectedCourseModules.map((module) => (
                                            <div key={module.id} className="rounded-xl border border-slate-200 p-3">
                                                <div className="mb-2 text-sm font-semibold text-slate-800">{module.title}</div>
                                                <div className="space-y-2">
                                                    {(module.lessons || []).map((lesson) => {
                                                        const progress = selectedCourseAssignment?.lesson_progress?.[lesson.id] || {};
                                                        const isCompleted = progress?.status === 'completed';
                                                        return (
                                                            <div key={lesson.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                                                                <div>
                                                                    <div className="text-sm font-medium text-slate-800">{lesson.title}</div>
                                                                    <div className="text-xs text-slate-500">
                                                                        {formatDuration(lesson.duration_seconds)} · {formatPercent(progress?.completion_ratio || 0)}
                                                                    </div>
                                                                </div>
                                                                <div className="flex items-center gap-2">
                                                                    <span className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ${
                                                                        isCompleted ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-600'
                                                                    }`}>
                                                                        {isCompleted ? 'Завершен' : 'Не завершен'}
                                                                    </span>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => { void openLesson(lesson.id); }}
                                                                        className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-slate-800"
                                                                    >
                                                                        Открыть урок
                                                                    </button>
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            </div>
                                        ))}
                                    </div>

                                    <div className="rounded-xl border border-slate-200 p-3">
                                        <div className="mb-2 text-sm font-semibold text-slate-800">Тесты</div>
                                        {selectedCourseTests.length === 0 ? (
                                            <div className="text-sm text-slate-500">Для курса пока нет тестов.</div>
                                        ) : (
                                            <div className="space-y-2">
                                                {selectedCourseTests.map((test) => {
                                                    const attempts = selectedCourseAssignment?.tests?.[test.id] || {};
                                                    return (
                                                        <div key={test.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                                                            <div>
                                                                <div className="text-sm font-medium text-slate-800">{test.title}</div>
                                                                <div className="text-xs text-slate-500">
                                                                    Порог: {formatPercent(test.pass_threshold)} · Попытки: {toNumber(attempts.attempts_used, 0)} / {toNumber(test.attempt_limit, 3)}
                                                                </div>
                                                            </div>
                                                            <button
                                                                type="button"
                                                                onClick={() => { void startTest(test.id); }}
                                                                className="rounded-lg bg-cyan-700 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-cyan-800"
                                                            >
                                                                Начать тест
                                                            </button>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}
                        </SectionCard>

                        {selectedLessonId && (
                            <SectionCard
                                title={lessonData?.lesson?.title || 'Урок'}
                                subtitle={lessonData?.lesson?.module_title || ''}
                                actions={lessonData?.lesson ? (
                                    <div className="flex flex-wrap items-center gap-2">
                                        <button
                                            type="button"
                                            onClick={() => setPlayer((prev) => ({ ...prev, isPlaying: !prev.isPlaying }))}
                                            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 transition hover:bg-slate-100"
                                        >
                                            {player.isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                                            {player.isPlaying ? 'Пауза' : 'Играть'}
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => { void completeLesson(); }}
                                            disabled={!lessonCanComplete}
                                            className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium ${
                                                lessonCanComplete
                                                    ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                                                    : 'cursor-not-allowed bg-slate-200 text-slate-500'
                                            }`}
                                        >
                                            <CheckCircle2 className="h-4 w-4" />
                                            Завершить урок
                                        </button>
                                    </div>
                                ) : null}
                            >
                                {lessonLoading ? (
                                    <div className="flex items-center gap-2 text-sm text-slate-500">
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                        Загрузка урока...
                                    </div>
                                ) : !lessonData?.lesson ? (
                                    <div className="text-sm text-slate-500">Выберите урок из структуры курса.</div>
                                ) : (
                                    <div className="space-y-4">
                                        <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                                <div className="flex items-center gap-2 text-xs text-slate-500">
                                                    <Clock3 className="h-4 w-4" /> Длительность
                                                </div>
                                                <div className="mt-1 text-sm font-semibold text-slate-800">
                                                    {formatDuration(lessonData.lesson.duration_seconds)}
                                                </div>
                                            </div>
                                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                                <div className="flex items-center gap-2 text-xs text-slate-500">
                                                    <Gauge className="h-4 w-4" /> Подтвержденный просмотр
                                                </div>
                                                <div className="mt-1 text-sm font-semibold text-slate-800">
                                                    {formatPercent(lessonCompletionRatio)}
                                                </div>
                                            </div>
                                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                                <div className="flex items-center gap-2 text-xs text-slate-500">
                                                    <Timer className="h-4 w-4" /> Heartbeat
                                                </div>
                                                <div className="mt-1 text-sm font-semibold text-slate-800">{heartbeatSeconds} сек</div>
                                            </div>
                                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                                <div className="flex items-center gap-2 text-xs text-slate-500">
                                                    <ShieldCheck className="h-4 w-4" /> Stale gap
                                                </div>
                                                <div className="mt-1 text-sm font-semibold text-slate-800">{staleGapSeconds} сек</div>
                                            </div>
                                        </div>

                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="mb-2 flex items-center justify-between gap-2 text-sm font-medium text-slate-700">
                                                <span>Позиция просмотра</span>
                                                <span>{formatDuration(player.positionSeconds)} / {formatDuration(lessonData.lesson.duration_seconds)}</span>
                                            </div>
                                            <input
                                                type="range"
                                                min={0}
                                                max={Math.max(1, toNumber(lessonData.lesson.duration_seconds, 0))}
                                                value={player.seekDraft ?? player.positionSeconds}
                                                onChange={(event) => {
                                                    const value = toNumber(event.target.value, 0);
                                                    setPlayer((prev) => ({ ...prev, seekDraft: value }));
                                                }}
                                                onMouseUp={(event) => {
                                                    const value = toNumber(event.currentTarget.value, 0);
                                                    void seekTo(value);
                                                }}
                                                onTouchEnd={(event) => {
                                                    const value = toNumber(event.currentTarget.value, 0);
                                                    void seekTo(value);
                                                }}
                                                className="w-full accent-blue-700"
                                            />
                                            <div className="mt-3 flex flex-wrap gap-2">
                                                <button
                                                    type="button"
                                                    onClick={() => { void seekTo(player.positionSeconds - 10); }}
                                                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-100"
                                                >
                                                    -10 сек
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => { void seekTo(player.positionSeconds + 10); }}
                                                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-100"
                                                >
                                                    +10 сек
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => { void syncHeartbeat(); }}
                                                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-100"
                                                >
                                                    Синхронизировать сейчас
                                                </button>
                                            </div>
                                            <div className="mt-2 text-xs text-slate-500">
                                                Если вкладка становится неактивной, плеер автоматически ставится на паузу.
                                            </div>
                                        </div>

                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="mb-2 text-sm font-semibold text-slate-800">Материалы урока</div>
                                            {Array.isArray(lessonData.materials) && lessonData.materials.length > 0 ? (
                                                <div className="space-y-2">
                                                    {lessonData.materials.map((material) => {
                                                        const Icon = materialIcon(material.material_type);
                                                        return (
                                                            <div key={material.id} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                                                                <div className="flex flex-wrap items-center justify-between gap-2">
                                                                    <div className="inline-flex items-center gap-2 text-sm font-medium text-slate-800">
                                                                        <Icon className="h-4 w-4 text-slate-600" />
                                                                        {material.title || `Материал #${material.id}`}
                                                                    </div>
                                                                    {material.url ? (
                                                                        <a
                                                                            href={material.url}
                                                                            target="_blank"
                                                                            rel="noreferrer"
                                                                            className="inline-flex items-center gap-1 rounded-lg bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
                                                                        >
                                                                            <Link2 className="h-3.5 w-3.5" />
                                                                            Открыть
                                                                        </a>
                                                                    ) : null}
                                                                </div>
                                                                {material.content_text ? (
                                                                    <div className="mt-2 rounded-lg border border-slate-200 bg-white p-2 text-sm text-slate-600">
                                                                        {material.content_text}
                                                                    </div>
                                                                ) : null}
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            ) : (
                                                <div className="text-sm text-slate-500">Материалы отсутствуют.</div>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </SectionCard>
                        )}

                        {currentAttemptId ? (
                            <SectionCard
                                title={`Тест: попытка #${toNumber(testAttemptData?.attempt?.attempt_no, 1)}`}
                                subtitle={`Порог прохождения: ${formatPercent(testAttemptData?.attempt?.pass_threshold)}`}
                                actions={(
                                    <button
                                        type="button"
                                        onClick={() => { void finishTest(); }}
                                        disabled={finishingTest}
                                        className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium ${
                                            finishingTest
                                                ? 'cursor-not-allowed bg-slate-200 text-slate-500'
                                                : 'bg-emerald-600 text-white hover:bg-emerald-700'
                                        }`}
                                    >
                                        {finishingTest ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCheck className="h-4 w-4" />}
                                        Завершить тест
                                    </button>
                                )}
                            >
                                {testLoading ? (
                                    <div className="flex items-center gap-2 text-sm text-slate-500">
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                        Подготовка теста...
                                    </div>
                                ) : (
                                    <div className="space-y-3">
                                        {currentAttemptQuestions.map((question, index) => (
                                            <div key={question.id} className="rounded-xl border border-slate-200 p-3">
                                                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                                                    <div className="text-sm font-semibold text-slate-900">
                                                        Вопрос {index + 1}. {question.prompt}
                                                    </div>
                                                    <div className="inline-flex items-center gap-2">
                                                        <span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">
                                                            {String(question.type || '').toUpperCase()}
                                                        </span>
                                                        <span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">
                                                            {toNumber(question.points, 1)} балл.
                                                        </span>
                                                    </div>
                                                </div>
                                                <QuestionEditor
                                                    question={question}
                                                    value={answers[question.id] || {}}
                                                    savingState={answerSavingState[question.id]}
                                                    onChange={(payload, immediate) => queueAnswerSave(question.id, payload, immediate)}
                                                />
                                                <div className="mt-2 text-xs">
                                                    {answerSavingState[question.id] === 'saving' ? (
                                                        <span className="text-blue-600">Сохраняем...</span>
                                                    ) : null}
                                                    {answerSavingState[question.id] === 'saved' ? (
                                                        <span className="text-emerald-600">Сохранено</span>
                                                    ) : null}
                                                    {answerSavingState[question.id] === 'error' ? (
                                                        <span className="text-rose-600">Ошибка сохранения</span>
                                                    ) : null}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </SectionCard>
                        ) : null}

                        {testResult ? (
                            <SectionCard title="Результат теста" subtitle="Серверная оценка попытки">
                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                        <div className="text-xs text-slate-500">Оценка</div>
                                        <div className="mt-1 text-xl font-semibold text-slate-900">
                                            {formatPercent(testResult?.summary?.score_percent)}
                                        </div>
                                    </div>
                                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                        <div className="text-xs text-slate-500">Порог</div>
                                        <div className="mt-1 text-xl font-semibold text-slate-900">
                                            {formatPercent(testResult?.summary?.pass_threshold)}
                                        </div>
                                    </div>
                                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                        <div className="text-xs text-slate-500">Статус</div>
                                        <div className={`mt-1 text-xl font-semibold ${testResult?.summary?.passed ? 'text-emerald-600' : 'text-rose-600'}`}>
                                            {testResult?.summary?.passed ? 'Пройден' : 'Не пройден'}
                                        </div>
                                    </div>
                                </div>
                                <div className="mt-3 text-sm text-slate-500">
                                    Время: {formatDuration(testResult?.summary?.duration_seconds)} · Завершено: {formatDateTime(testResult?.summary?.finished_at)}
                                </div>
                                {testResult?.detail?.certificate ? (
                                    <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3">
                                        <div className="text-sm font-semibold text-emerald-800">
                                            Сертификат #{testResult.detail.certificate.certificate_number}
                                        </div>
                                        <div className="mt-1 text-xs text-emerald-700">
                                            Статус: {testResult.detail.certificate.status} · Проверка: {testResult.detail.certificate.verify_url}
                                        </div>
                                    </div>
                                ) : null}
                            </SectionCard>
                        ) : null}
                    </div>
                </div>
            )}

            {isLearner && activeTab === 'certificates' && (
                <SectionCard title="Сертификаты" subtitle="PDF сертификаты с QR verify токеном">
                    {certificatesLoading && !certificatesLoaded ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Загрузка сертификатов...
                        </div>
                    ) : certificates.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                            Сертификаты пока не выпущены.
                        </div>
                    ) : (
                        <div className="space-y-2">
                            {certificates.map((cert) => (
                                <div key={cert.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 p-3">
                                    <div>
                                        <div className="text-sm font-semibold text-slate-900">{cert.certificate_number}</div>
                                        <div className="mt-1 text-xs text-slate-500">
                                            Статус: {cert.status} · Выдан: {formatDateTime(cert.issued_at)} · Score: {cert.score_percent !== null ? `${cert.score_percent}%` : '—'}
                                        </div>
                                        <a
                                            href={cert.verify_url}
                                            target="_blank"
                                            rel="noreferrer"
                                            className="mt-1 inline-flex items-center gap-1 text-xs text-blue-700 hover:text-blue-800"
                                        >
                                            <Link2 className="h-3.5 w-3.5" />
                                            Verify
                                        </a>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => { void downloadCertificate(cert.id, cert.certificate_number); }}
                                        disabled={downloadingCertificateId === cert.id}
                                        className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium ${
                                            downloadingCertificateId === cert.id
                                                ? 'cursor-not-allowed bg-slate-200 text-slate-500'
                                                : 'bg-slate-900 text-white hover:bg-slate-800'
                                        }`}
                                    >
                                        {downloadingCertificateId === cert.id ? (
                                            <Loader2 className="h-4 w-4 animate-spin" />
                                        ) : (
                                            <Download className="h-4 w-4" />
                                        )}
                                        Скачать PDF
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}
                </SectionCard>
            )}

            {isLearner && activeTab === 'notifications' && (
                <SectionCard title="Уведомления" subtitle="Системные события по обучению">
                    {notificationsLoading && !notificationsLoaded ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Загрузка уведомлений...
                        </div>
                    ) : notifications.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                            Уведомлений нет.
                        </div>
                    ) : (
                        <div className="space-y-2">
                            {notifications.map((item) => (
                                <div key={item.id} className={`rounded-xl border p-3 ${item.is_read ? 'border-slate-200 bg-white' : 'border-cyan-200 bg-cyan-50/60'}`}>
                                    <div className="flex flex-wrap items-start justify-between gap-2">
                                        <div>
                                            <div className="text-sm font-semibold text-slate-900">{item.title}</div>
                                            <div className="mt-1 text-xs text-slate-500">{item.message || 'Без дополнительного текста'}</div>
                                            <div className="mt-1 text-[11px] text-slate-400">{formatDateTime(item.created_at)}</div>
                                        </div>
                                        {!item.is_read ? (
                                            <button
                                                type="button"
                                                onClick={() => { void markNotificationRead(item.id); }}
                                                className="rounded-lg bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
                                            >
                                                Прочитано
                                            </button>
                                        ) : (
                                            <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">
                                                <Check className="mr-1 h-3.5 w-3.5" />
                                                Прочитано
                                            </span>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </SectionCard>
            )}

            {isManager && activeTab === 'admin_courses' && (
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-[0.9fr_1.1fr]">
                    <SectionCard title="Создание курса" subtitle="Быстрый MVP-конструктор">
                        <div className="space-y-3">
                            <input
                                value={newCourseForm.title}
                                onChange={(event) => setNewCourseForm((prev) => ({ ...prev, title: event.target.value }))}
                                placeholder="Название курса"
                                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                            />
                            <textarea
                                value={newCourseForm.description}
                                onChange={(event) => setNewCourseForm((prev) => ({ ...prev, description: event.target.value }))}
                                placeholder="Описание"
                                rows={3}
                                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                            />
                            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                                <input
                                    value={newCourseForm.category}
                                    onChange={(event) => setNewCourseForm((prev) => ({ ...prev, category: event.target.value }))}
                                    placeholder="Категория"
                                    className="rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                                />
                                <input
                                    type="number"
                                    value={newCourseForm.pass_threshold}
                                    onChange={(event) => setNewCourseForm((prev) => ({ ...prev, pass_threshold: event.target.value }))}
                                    placeholder="Порог %"
                                    className="rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                                />
                                <input
                                    type="number"
                                    value={newCourseForm.attempt_limit}
                                    onChange={(event) => setNewCourseForm((prev) => ({ ...prev, attempt_limit: event.target.value }))}
                                    placeholder="Лимит попыток"
                                    className="rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                                />
                            </div>
                            <textarea
                                value={newCourseForm.blueprint_json}
                                onChange={(event) => setNewCourseForm((prev) => ({ ...prev, blueprint_json: event.target.value }))}
                                placeholder='Опционально: {"modules":[...],"tests":[...]}'
                                rows={6}
                                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-xs font-mono focus:border-blue-500 focus:outline-none"
                            />
                            <button
                                type="button"
                                onClick={() => { void createCourse(); }}
                                disabled={createCourseLoading}
                                className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium ${
                                    createCourseLoading
                                        ? 'cursor-not-allowed bg-slate-200 text-slate-500'
                                        : 'bg-blue-700 text-white hover:bg-blue-800'
                                }`}
                            >
                                {createCourseLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <GraduationCap className="h-4 w-4" />}
                                Создать курс
                            </button>
                        </div>
                    </SectionCard>

                    <SectionCard title="Каталог курсов" subtitle="Управление публикацией">
                        {adminCoursesLoading && !adminCoursesLoaded ? (
                            <div className="flex items-center gap-2 text-sm text-slate-500">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Загрузка курсов...
                            </div>
                        ) : adminCourses.length === 0 ? (
                            <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                                Курсов нет.
                            </div>
                        ) : (
                            <div className="space-y-2">
                                {adminCourses.map((course) => (
                                    <div key={course.id} className="rounded-xl border border-slate-200 p-3">
                                        <div className="flex flex-wrap items-start justify-between gap-2">
                                            <div>
                                                <div className="text-sm font-semibold text-slate-900">{course.title}</div>
                                                <div className="mt-1 text-xs text-slate-500">
                                                    Статус: {statusLabel(course.status)} · Версия: {course.current_version?.version_number || '—'}
                                                </div>
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => { void publishCourse(course.id, course.current_version_id); }}
                                                className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
                                            >
                                                Publish
                                            </button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </SectionCard>
                </div>
            )}

            {isManager && activeTab === 'admin_assignments' && (
                <SectionCard title="Назначение курсов" subtitle="Назначайте курс операторам и trainee">
                    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[280px_1fr]">
                        <div className="space-y-3">
                            <select
                                value={assignmentForm.course_id}
                                onChange={(event) => setAssignmentForm((prev) => ({ ...prev, course_id: event.target.value }))}
                                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                            >
                                <option value="">Выберите курс</option>
                                {adminCourses.map((course) => (
                                    <option key={course.id} value={course.id}>
                                        {course.title}
                                    </option>
                                ))}
                            </select>
                            <input
                                type="datetime-local"
                                value={assignmentForm.due_at}
                                onChange={(event) => setAssignmentForm((prev) => ({ ...prev, due_at: event.target.value }))}
                                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                            />
                            <button
                                type="button"
                                onClick={() => { void assignCourse(); }}
                                disabled={assigning}
                                className={`inline-flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-medium ${
                                    assigning
                                        ? 'cursor-not-allowed bg-slate-200 text-slate-500'
                                        : 'bg-blue-700 text-white hover:bg-blue-800'
                                }`}
                            >
                                {assigning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Users className="h-4 w-4" />}
                                Назначить
                            </button>
                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                                Выбрано пользователей: {assignmentForm.user_ids.length}
                            </div>
                        </div>

                        <div className="rounded-xl border border-slate-200 p-3">
                            <label className="mb-2 block">
                                <span className="mb-1 inline-flex items-center gap-1 text-xs font-medium text-slate-500">
                                    <Search className="h-3 w-3" /> Поиск сотрудника
                                </span>
                                <input
                                    value={userSearch}
                                    onChange={(event) => setUserSearch(event.target.value)}
                                    className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                                    placeholder="Имя, логин..."
                                />
                            </label>
                            {assignableUsersLoading && !assignableUsersLoaded ? (
                                <div className="flex items-center gap-2 text-sm text-slate-500">
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Загрузка сотрудников...
                                </div>
                            ) : (
                                <div className="max-h-80 space-y-2 overflow-auto pr-1">
                                    {visibleUsers.map((employee) => {
                                        const checked = assignmentForm.user_ids.includes(employee.id);
                                        return (
                                            <label key={employee.id} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 text-sm hover:bg-slate-50">
                                                <input
                                                    type="checkbox"
                                                    checked={checked}
                                                    onChange={(event) => {
                                                        setAssignmentForm((prev) => {
                                                            const existing = new Set(prev.user_ids);
                                                            if (event.target.checked) existing.add(employee.id);
                                                            else existing.delete(employee.id);
                                                            return { ...prev, user_ids: Array.from(existing) };
                                                        });
                                                    }}
                                                />
                                                <span className="font-medium text-slate-800">{employee.name}</span>
                                                <span className="text-xs text-slate-500">{employee.role}</span>
                                            </label>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    </div>
                </SectionCard>
            )}

            {isManager && activeTab === 'admin_progress' && (
                <SectionCard title="Прогресс обучения" subtitle="По назначенным курсам и сотрудникам">
                    {adminProgressLoading && !adminProgressLoaded ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Загрузка прогресса...
                        </div>
                    ) : adminProgress.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                            Нет данных по прогрессу.
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="min-w-full border-separate border-spacing-0 text-sm">
                                <thead>
                                    <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                                        <th className="border-b border-slate-200 px-3 py-2">Сотрудник</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Курс</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Прогресс</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Тесты</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Дедлайн</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Статус</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {adminProgress.map((row) => {
                                        const deadlineMeta = deadlineMetaFor(row.deadline_status);
                                        return (
                                            <tr key={row.assignment_id} className="text-slate-700">
                                                <td className="border-b border-slate-100 px-3 py-2">
                                                    <div className="font-medium">{row.user_name}</div>
                                                    <div className="text-xs text-slate-500">{row.user_role}</div>
                                                </td>
                                                <td className="border-b border-slate-100 px-3 py-2">{row.course_title}</td>
                                                <td className="border-b border-slate-100 px-3 py-2">
                                                    <div className="h-1.5 w-40 overflow-hidden rounded-full bg-slate-200">
                                                        <div
                                                            className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-600"
                                                            style={{ width: `${clamp(toNumber(row.progress_percent, 0), 0, 100)}%` }}
                                                        />
                                                    </div>
                                                    <div className="mt-1 text-xs text-slate-500">
                                                        {formatPercent(row.progress_percent)} · {toNumber(row.completed_lessons, 0)} / {toNumber(row.total_lessons, 0)}
                                                    </div>
                                                </td>
                                                <td className="border-b border-slate-100 px-3 py-2 text-xs text-slate-600">
                                                    {toNumber(row.passed_tests, 0)} / {toNumber(row.total_tests, 0)}
                                                </td>
                                                <td className="border-b border-slate-100 px-3 py-2 text-xs text-slate-600">
                                                    {formatDateTime(row.due_at)}
                                                </td>
                                                <td className="border-b border-slate-100 px-3 py-2">
                                                    <span className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${deadlineMeta.badge}`}>
                                                        {deadlineMeta.label}
                                                    </span>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </SectionCard>
            )}

            {isManager && activeTab === 'admin_attempts' && (
                <SectionCard title="Попытки тестов" subtitle="История прохождений по последним попыткам">
                    {adminAttemptsLoading && !adminAttemptsLoaded ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Загрузка попыток...
                        </div>
                    ) : adminAttempts.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                            Попытки отсутствуют.
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="min-w-full border-separate border-spacing-0 text-sm">
                                <thead>
                                    <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                                        <th className="border-b border-slate-200 px-3 py-2">Сотрудник</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Курс / Тест</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Попытка</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Score</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Время</th>
                                        <th className="border-b border-slate-200 px-3 py-2">Статус</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {adminAttempts.map((attempt) => (
                                        <tr key={attempt.attempt_id} className="text-slate-700">
                                            <td className="border-b border-slate-100 px-3 py-2">
                                                <div className="font-medium">{attempt.user_name}</div>
                                                <div className="text-xs text-slate-500">{attempt.user_role}</div>
                                            </td>
                                            <td className="border-b border-slate-100 px-3 py-2">
                                                <div className="font-medium">{attempt.course_title}</div>
                                                <div className="text-xs text-slate-500">{attempt.test_title}</div>
                                            </td>
                                            <td className="border-b border-slate-100 px-3 py-2">#{toNumber(attempt.attempt_no, 1)}</td>
                                            <td className="border-b border-slate-100 px-3 py-2">
                                                {attempt.score_percent !== null ? `${attempt.score_percent}%` : '—'}
                                            </td>
                                            <td className="border-b border-slate-100 px-3 py-2 text-xs text-slate-600">
                                                {formatDuration(attempt.duration_seconds)} · {formatDateTime(attempt.finished_at || attempt.started_at)}
                                            </td>
                                            <td className="border-b border-slate-100 px-3 py-2">
                                                {attempt.passed === null ? (
                                                    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">
                                                        <Clock3 className="h-3.5 w-3.5" />
                                                        {statusLabel(attempt.status)}
                                                    </span>
                                                ) : attempt.passed ? (
                                                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-1 text-xs text-emerald-700">
                                                        <Check className="h-3.5 w-3.5" />
                                                        Passed
                                                    </span>
                                                ) : (
                                                    <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-1 text-xs text-rose-700">
                                                        <X className="h-3.5 w-3.5" />
                                                        Failed
                                                    </span>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </SectionCard>
            )}

            {isManager && activeTab === 'admin_deadlines' && (
                <SectionCard title="Дедлайны" subtitle="Контроль сроков и цветовых статусов">
                    {adminProgressLoading && !adminProgressLoaded ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Загрузка дедлайнов...
                        </div>
                    ) : deadlinesRows.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                            Дедлайны пока не установлены.
                        </div>
                    ) : (
                        <div className="space-y-2">
                            {deadlinesRows.map((row) => {
                                const meta = deadlineMetaFor(row.deadline_status);
                                return (
                                    <div key={`deadline-${row.assignment_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 p-3">
                                        <div>
                                            <div className="text-sm font-semibold text-slate-900">{row.user_name} · {row.course_title}</div>
                                            <div className="mt-1 text-xs text-slate-500">
                                                Дедлайн: {formatDateTime(row.due_at)} · Завершено: {formatDateTime(row.completed_at)}
                                            </div>
                                        </div>
                                        <span className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${meta.badge}`}>
                                            {meta.label}
                                        </span>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </SectionCard>
            )}

            {isManager && activeTab === 'admin_materials' && (
                <SectionCard title="Загрузка материалов" subtitle="GCS upload и привязка к уроку">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                        <input
                            value={uploadForm.lesson_id}
                            onChange={(event) => setUploadForm((prev) => ({ ...prev, lesson_id: event.target.value }))}
                            placeholder="lesson_id (опционально)"
                            className="rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        />
                        <input
                            value={uploadForm.title}
                            onChange={(event) => setUploadForm((prev) => ({ ...prev, title: event.target.value }))}
                            placeholder="Название материала"
                            className="rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        />
                        <select
                            value={uploadForm.material_type}
                            onChange={(event) => setUploadForm((prev) => ({ ...prev, material_type: event.target.value }))}
                            className="rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        >
                            <option value="file">File</option>
                            <option value="video">Video</option>
                            <option value="pdf">PDF</option>
                            <option value="link">Link</option>
                            <option value="text">Text</option>
                        </select>
                        <input
                            value={uploadForm.position}
                            onChange={(event) => setUploadForm((prev) => ({ ...prev, position: event.target.value }))}
                            placeholder="Position"
                            className="rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        />
                        <input
                            type="file"
                            multiple
                            onChange={(event) => setUploadForm((prev) => ({ ...prev, files: Array.from(event.target.files || []) }))}
                            className="md:col-span-2 rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        />
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-3">
                        <div className="text-xs text-slate-500">Файлов к загрузке: {uploadForm.files.length}</div>
                        <button
                            type="button"
                            onClick={() => { void uploadMaterials(); }}
                            disabled={uploadingMaterials}
                            className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium ${
                                uploadingMaterials
                                    ? 'cursor-not-allowed bg-slate-200 text-slate-500'
                                    : 'bg-blue-700 text-white hover:bg-blue-800'
                            }`}
                        >
                            {uploadingMaterials ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                            Upload
                        </button>
                    </div>
                </SectionCard>
            )}

            {isManager && isFullAdmin && activeTab === 'admin_revoke' && (
                <SectionCard title="Отзыв сертификата" subtitle="Доступно только admin / super_admin">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                        <input
                            value={revokeForm.certificate_id}
                            onChange={(event) => setRevokeForm((prev) => ({ ...prev, certificate_id: event.target.value }))}
                            placeholder="certificate_id"
                            className="rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        />
                        <input
                            value={revokeForm.reason}
                            onChange={(event) => setRevokeForm((prev) => ({ ...prev, reason: event.target.value }))}
                            placeholder="Причина отзыва"
                            className="rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        />
                    </div>
                    <button
                        type="button"
                        onClick={() => { void revokeCertificate(); }}
                        disabled={revoking}
                        className={`mt-3 inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium ${
                            revoking
                                ? 'cursor-not-allowed bg-slate-200 text-slate-500'
                                : 'bg-rose-600 text-white hover:bg-rose-700'
                        }`}
                    >
                        {revoking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Ban className="h-4 w-4" />}
                        Отозвать
                    </button>
                </SectionCard>
            )}
        </div>
    );
};

export default LmsView;