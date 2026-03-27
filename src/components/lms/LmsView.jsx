import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Award,
    Ban,
    Bell,
    BookOpen,
    CalendarDays,
    Check,
    CheckCircle2,
    ClipboardList,
    Clock3,
    Download,
    Gauge,
    GraduationCap,
    Loader2,
    Pause,
    Play,
    RefreshCw,
    Search,
    ShieldCheck,
    Timer,
    Upload,
    Users,
    X
} from 'lucide-react';

const LEARNER = new Set(['operator', 'trainee']);
const MANAGER = new Set(['sv', 'trainer', 'admin', 'super_admin']);
const FULL_ADMIN = new Set(['admin', 'super_admin']);
const DEADLINE_STYLE = {
    on_time: 'bg-emerald-100 text-emerald-700 border-emerald-200',
    late_completed: 'bg-amber-100 text-amber-700 border-amber-200',
    overdue: 'bg-rose-100 text-rose-700 border-rose-200',
    unknown: 'bg-slate-100 text-slate-700 border-slate-200'
};
const DEADLINE_LABEL = {
    on_time: 'В срок',
    late_completed: 'С опозданием',
    overdue: 'Просрочено',
    unknown: 'Без срока'
};

const toNum = (v, d = 0) => (Number.isFinite(Number(v)) ? Number(v) : d);
const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
const pct = (v) => `${toNum(v, 0).toFixed(0)}%`;
const fmtDate = (iso) => {
    if (!iso) return '—';
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return '—';
    return dt.toLocaleString('ru-RU', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};
const fmtDur = (s) => {
    const n = Math.max(0, Math.floor(toNum(s, 0)));
    const h = Math.floor(n / 3600);
    const m = Math.floor((n % 3600) / 60);
    const sec = n % 60;
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
};
const json = (v) => {
    try {
        return JSON.stringify(v ?? null);
    } catch {
        return '';
    }
};
const isAbort = (e) => e?.code === 'ERR_CANCELED' || e?.name === 'CanceledError';
const optIds = (payload) => {
    if (!payload || typeof payload !== 'object') return [];
    if (Array.isArray(payload.option_ids)) return payload.option_ids.map((x) => toNum(x, 0)).filter(Boolean);
    if (payload.option_id !== undefined && payload.option_id !== null) return [toNum(payload.option_id, 0)].filter(Boolean);
    return [];
};

const Card = ({ title, subtitle, actions, children }) => (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
            <div>
                <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
                {subtitle ? <p className="text-xs text-slate-500">{subtitle}</p> : null}
            </div>
            {actions}
        </div>
        {children}
    </section>
);

const Tab = ({ icon: Icon, active, label, onClick }) => (
    <button
        type="button"
        onClick={onClick}
        className={`inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-sm ${
            active ? 'border-blue-700 bg-blue-700 text-white' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
        }`}
    >
        <Icon className="h-4 w-4" />
        <span>{label}</span>
    </button>
);

const QBlock = ({ q, value, state, onChange }) => {
    const t = String(q?.type || '').toLowerCase();
    const opts = Array.isArray(q?.options) ? q.options : [];
    if (t === 'single') {
        const selected = optIds(value)[0] || 0;
        return (
            <div className="space-y-2">
                {opts.map((o) => (
                    <button
                        key={o.id}
                        type="button"
                        onClick={() => onChange({ option_id: o.id }, true)}
                        className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${selected === toNum(o.id, 0) ? 'border-blue-400 bg-blue-50' : 'border-slate-200'}`}
                    >
                        {o.text}
                    </button>
                ))}
            </div>
        );
    }
    if (t === 'multiple') {
        const s = new Set(optIds(value));
        return (
            <div className="space-y-2">
                {opts.map((o) => {
                    const id = toNum(o.id, 0);
                    const on = s.has(id);
                    return (
                        <button
                            key={o.id}
                            type="button"
                            onClick={() => {
                                const next = new Set(s);
                                if (on) next.delete(id);
                                else next.add(id);
                                onChange({ option_ids: Array.from(next) }, true);
                            }}
                            className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${on ? 'border-cyan-400 bg-cyan-50' : 'border-slate-200'}`}
                        >
                            {o.text}
                        </button>
                    );
                })}
            </div>
        );
    }
    if (t === 'true_false') {
        return (
            <div className="grid grid-cols-2 gap-2">
                {opts.map((o) => (
                    <button
                        key={o.id}
                        type="button"
                        onClick={() => onChange({ option_id: o.id }, true)}
                        className={`rounded-lg border px-3 py-2 text-sm ${optIds(value)[0] === toNum(o.id, 0) ? 'border-blue-500 bg-blue-600 text-white' : 'border-slate-200'}`}
                    >
                        {o.text}
                    </button>
                ))}
            </div>
        );
    }
    if (t === 'matching') {
        const pairs = value?.pairs && typeof value.pairs === 'object' ? value.pairs : {};
        const rights = opts.map((o) => ({ value: String(o.key || ''), label: String(o.text || '') })).filter((x) => x.value);
        return (
            <div className="space-y-2">
                {opts.filter((o) => o?.key).map((left) => (
                    <label key={left.id} className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_180px]">
                        <span className="text-sm text-slate-700">{left.text}</span>
                        <select
                            value={pairs[left.key] || ''}
                            onChange={(e) => onChange({ pairs: { ...pairs, [left.key]: e.target.value } }, false)}
                            className="rounded-lg border border-slate-300 px-2 py-2 text-sm"
                        >
                            <option value="">Выберите</option>
                            {rights.map((r) => (
                                <option key={`${left.id}-${r.value}`} value={r.value}>{r.label}</option>
                            ))}
                        </select>
                    </label>
                ))}
            </div>
        );
    }
    return (
        <textarea
            value={typeof value?.text === 'string' ? value.text : ''}
            onChange={(e) => onChange({ text: e.target.value }, false)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            rows={4}
        />
    );
};

const LmsView = ({ user, apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const role = String(user?.role || '').toLowerCase();
    const isLearner = LEARNER.has(role);
    const isManager = MANAGER.has(role);
    const isFullAdmin = FULL_ADMIN.has(role);

    const tabs = useMemo(() => {
        const list = [];
        if (isLearner) {
            list.push(
                { id: 'home', label: 'Обзор', icon: Gauge },
                { id: 'courses', label: 'Курсы', icon: BookOpen },
                { id: 'certs', label: 'Сертификаты', icon: Award },
                { id: 'notes', label: 'Уведомления', icon: Bell }
            );
        }
        if (isManager) {
            list.push(
                { id: 'admin_courses', label: 'Курсы', icon: GraduationCap },
                { id: 'admin_assign', label: 'Назначения', icon: Users },
                { id: 'admin_progress', label: 'Прогресс', icon: ClipboardList },
                { id: 'admin_attempts', label: 'Попытки', icon: Clock3 },
                { id: 'admin_deadlines', label: 'Дедлайны', icon: CalendarDays },
                { id: 'admin_materials', label: 'Материалы', icon: Upload }
            );
            if (isFullAdmin) list.push({ id: 'admin_revoke', label: 'Отзыв', icon: Ban });
        }
        return list;
    }, [isLearner, isManager, isFullAdmin]);

    const [tab, setTab] = useState(isLearner ? 'home' : 'admin_courses');

    const headers = useMemo(() => withAccessTokenHeader({ 'X-API-Key': user?.apiKey, 'X-User-Id': user?.id }), [withAccessTokenHeader, user?.apiKey, user?.id]);
    const mounted = useRef(true);
    const inflight = useRef(new Map());
    const cache = useRef(new Map());
    const controllers = useRef(new Map());
    const timers = useRef(new Map());
    const lastSaved = useRef(new Map());
    const dirty = useRef(new Set());
    const answersRef = useRef({});
    const lessonIdRef = useRef(null);
    const posRef = useRef(0);
    const hbBusy = useRef(false);
    const visibleRef = useRef(true);

    useEffect(() => () => {
        mounted.current = false;
        controllers.current.forEach((c) => c.abort());
        timers.current.forEach((t) => window.clearTimeout(t));
    }, []);

    const api = useCallback(async (method, path, opt = {}) => {
        const m = String(method || 'GET').toUpperCase();
        const { data = null, params, signal, cacheMs = 0, dedupe = true, responseType } = opt;
        const key = `${m}|${path}|${json(params)}|${json(data)}|${responseType || ''}`;
        if (m === 'GET' && cacheMs > 0) {
            const hit = cache.current.get(key);
            if (hit && hit.exp > Date.now()) return hit.payload;
        }
        if (dedupe && !signal && inflight.current.has(key)) return inflight.current.get(key);
        const p = axios({
            method: m,
            url: `${apiBaseUrl}${path}`,
            headers,
            withCredentials: true,
            data: data === null ? undefined : data,
            params,
            responseType,
            signal
        }).then((r) => (responseType ? r : r.data)).finally(() => inflight.current.delete(key));
        if (dedupe && !signal) inflight.current.set(key, p);
        const out = await p;
        if (m === 'GET' && cacheMs > 0) cache.current.set(key, { payload: out, exp: Date.now() + cacheMs });
        return out;
    }, [apiBaseUrl, headers]);

    const toastErr = useCallback((e, fallback) => {
        if (isAbort(e)) return;
        showToast?.(e?.response?.data?.error || fallback, 'error');
    }, [showToast]);

    const runExclusive = useCallback(async (slot, fn) => {
        const prev = controllers.current.get(slot);
        if (prev) prev.abort();
        const c = new AbortController();
        controllers.current.set(slot, c);
        try {
            return await fn(c.signal);
        } finally {
            if (controllers.current.get(slot) === c) controllers.current.delete(slot);
        }
    }, []);

    const dropLmsCache = useCallback(() => {
        for (const k of Array.from(cache.current.keys())) {
            if (k.includes('/api/lms/')) cache.current.delete(k);
        }
    }, []);

    const [home, setHome] = useState(null);
    const [courses, setCourses] = useState([]);
    const [course, setCourse] = useState(null);
    const [lesson, setLesson] = useState(null);
    const [player, setPlayer] = useState({ isPlaying: false, pos: 0, maxSeen: 0, draft: null });
    const [attempt, setAttempt] = useState(null);
    const [answers, setAnswers] = useState({});
    const [saveState, setSaveState] = useState({});
    const [result, setResult] = useState(null);
    const [certs, setCerts] = useState([]);
    const [notes, setNotes] = useState([]);
    const [loading, setLoading] = useState({});

    const [adminCourses, setAdminCourses] = useState([]);
    const [adminProgress, setAdminProgress] = useState([]);
    const [adminAttempts, setAdminAttempts] = useState([]);
    const [users, setUsers] = useState([]);
    const [newCourse, setNewCourse] = useState({ title: '', description: '', category: '', pass_threshold: 80, attempt_limit: 3, blueprint: '' });
    const [assign, setAssign] = useState({ course_id: '', due_at: '', user_ids: [] });
    const [upload, setUpload] = useState({ lesson_id: '', title: '', material_type: 'file', position: '', files: [] });
    const [revoke, setRevoke] = useState({ certificate_id: '', reason: 'Revoked by administrator' });
    const [courseQuery, setCourseQuery] = useState('');
    const [userQuery, setUserQuery] = useState('');

    useEffect(() => { answersRef.current = answers; }, [answers]);
    useEffect(() => { lessonIdRef.current = lesson?.lesson?.id || null; }, [lesson?.lesson?.id]);
    useEffect(() => { posRef.current = toNum(player.pos, 0); }, [player.pos]);

    const loadHome = useCallback(async (force = false) => {
        if (!isLearner) return;
        setLoading((p) => ({ ...p, home: true }));
        try {
            const data = await api('GET', '/api/lms/home', { cacheMs: force ? 0 : 10000 });
            if (!mounted.current) return;
            setHome(data || null);
        } catch (e) { toastErr(e, 'LMS home error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, home: false })); }
    }, [api, isLearner, toastErr]);

    const loadCourses = useCallback(async (force = false) => {
        if (!isLearner) return;
        setLoading((p) => ({ ...p, courses: true }));
        try {
            const data = await api('GET', '/api/lms/courses', { cacheMs: force ? 0 : 10000 });
            if (!mounted.current) return;
            setCourses(Array.isArray(data?.courses) ? data.courses : []);
        } catch (e) { toastErr(e, 'Courses load error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, courses: false })); }
    }, [api, isLearner, toastErr]);

    const loadCerts = useCallback(async (force = false) => {
        if (!isLearner) return;
        setLoading((p) => ({ ...p, certs: true }));
        try {
            const data = await api('GET', '/api/lms/certificates', { cacheMs: force ? 0 : 12000 });
            if (!mounted.current) return;
            setCerts(Array.isArray(data?.certificates) ? data.certificates : []);
        } catch (e) { toastErr(e, 'Certificates load error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, certs: false })); }
    }, [api, isLearner, toastErr]);

    const loadNotes = useCallback(async (force = false) => {
        if (!isLearner) return;
        setLoading((p) => ({ ...p, notes: true }));
        try {
            const data = await api('GET', '/api/lms/notifications', { params: { limit: 200 }, cacheMs: force ? 0 : 8000 });
            if (!mounted.current) return;
            setNotes(Array.isArray(data?.notifications) ? data.notifications : []);
        } catch (e) { toastErr(e, 'Notifications load error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, notes: false })); }
    }, [api, isLearner, toastErr]);

    const loadAdminCourses = useCallback(async (force = false) => {
        if (!isManager) return;
        setLoading((p) => ({ ...p, admin_courses: true }));
        try {
            const data = await api('GET', '/api/lms/admin/courses', { cacheMs: force ? 0 : 10000 });
            if (!mounted.current) return;
            setAdminCourses(Array.isArray(data?.courses) ? data.courses : []);
        } catch (e) { toastErr(e, 'Admin courses error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, admin_courses: false })); }
    }, [api, isManager, toastErr]);

    const loadAdminProgress = useCallback(async (force = false) => {
        if (!isManager) return;
        setLoading((p) => ({ ...p, admin_progress: true }));
        try {
            const data = await api('GET', '/api/lms/admin/progress', { cacheMs: force ? 0 : 10000 });
            if (!mounted.current) return;
            setAdminProgress(Array.isArray(data?.rows) ? data.rows : []);
        } catch (e) { toastErr(e, 'Admin progress error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, admin_progress: false })); }
    }, [api, isManager, toastErr]);

    const loadAdminAttempts = useCallback(async (force = false) => {
        if (!isManager) return;
        setLoading((p) => ({ ...p, admin_attempts: true }));
        try {
            const data = await api('GET', '/api/lms/admin/attempts', { cacheMs: force ? 0 : 10000 });
            if (!mounted.current) return;
            setAdminAttempts(Array.isArray(data?.attempts) ? data.attempts : []);
        } catch (e) { toastErr(e, 'Admin attempts error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, admin_attempts: false })); }
    }, [api, isManager, toastErr]);

    const loadUsers = useCallback(async (force = false) => {
        if (!isManager) return;
        setLoading((p) => ({ ...p, users: true }));
        try {
            const data = await api('GET', '/api/admin/users', { cacheMs: force ? 0 : 60000 });
            if (!mounted.current) return;
            const raw = Array.isArray(data?.users) ? data.users : [];
            setUsers(raw.filter((u) => LEARNER.has(String(u?.role || '').toLowerCase())));
        } catch (e) { toastErr(e, 'Users load error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, users: false })); }
    }, [api, isManager, toastErr]);

    const openCourse = useCallback(async (courseId) => {
        const id = toNum(courseId, 0);
        if (!id) return;
        setLoading((p) => ({ ...p, course: true }));
        setCourse(null);
        setLesson(null);
        setAttempt(null);
        setResult(null);
        setAnswers({});
        setSaveState({});
        dirty.current.clear();
        lastSaved.current.clear();
        timers.current.forEach((t) => window.clearTimeout(t));
        timers.current.clear();
        try {
            const data = await runExclusive('course', (signal) => api('GET', `/api/lms/courses/${id}`, { signal, dedupe: false }));
            if (!mounted.current) return;
            setCourse(data?.course || null);
            setTab('courses');
        } catch (e) { toastErr(e, 'Open course error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, course: false })); }
    }, [api, runExclusive, toastErr]);

    const sendLessonEvent = useCallback(async (eventType, payload = {}) => {
        const lessonId = lessonIdRef.current;
        if (!lessonId) return;
        try {
            await api('POST', `/api/lms/lessons/${lessonId}/event`, {
                data: { event_type: eventType, payload, client_ts: new Date().toISOString() },
                dedupe: false
            });
        } catch (e) {
            if (e?.response?.status === 409) {
                const allowed = toNum(e?.response?.data?.allowed_position, posRef.current);
                setPlayer((p) => ({ ...p, pos: allowed, isPlaying: false, draft: null }));
                showToast?.('Перемотка вперед заблокирована сервером anti-cheat.', 'info');
                return;
            }
            toastErr(e, 'Lesson event error');
        }
    }, [api, showToast, toastErr]);

    const openLesson = useCallback(async (lessonId) => {
        const id = toNum(lessonId, 0);
        if (!id) return;
        setLoading((p) => ({ ...p, lesson: true }));
        try {
            const data = await runExclusive('lesson', (signal) => api('GET', `/api/lms/lessons/${id}`, { signal, dedupe: false }));
            if (!mounted.current) return;
            setLesson(data || null);
            const startPos = toNum(data?.progress?.max_position_seconds, 0);
            const confirmed = toNum(data?.progress?.confirmed_seconds, startPos);
            setPlayer((p) => ({ ...p, isPlaying: false, pos: startPos, maxSeen: Math.max(startPos, confirmed), draft: null }));
            await sendLessonEvent('open', { lesson_id: id });
        } catch (e) { toastErr(e, 'Open lesson error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, lesson: false })); }
    }, [api, runExclusive, sendLessonEvent, toastErr]);

    const heartbeat = useCallback(async () => {
        const id = lessonIdRef.current;
        if (!id || hbBusy.current) return;
        hbBusy.current = true;
        try {
            const data = await api('POST', `/api/lms/lessons/${id}/heartbeat`, {
                data: { position_seconds: posRef.current, tab_visible: visibleRef.current, client_ts: new Date().toISOString() },
                dedupe: false
            });
            if (!mounted.current) return;
            const serverPos = toNum(data?.position_seconds, posRef.current);
            setPlayer((p) => ({ ...p, pos: serverPos, maxSeen: Math.max(p.maxSeen, serverPos) }));
            setLesson((prev) => {
                if (!prev) return prev;
                const ratio = toNum(data?.completion_ratio, toNum(prev?.progress?.completion_ratio, 0));
                return {
                    ...prev,
                    progress: {
                        ...(prev.progress || {}),
                        max_position_seconds: serverPos,
                        confirmed_seconds: Math.max(toNum(prev.progress?.confirmed_seconds, 0), serverPos),
                        completion_ratio: ratio,
                        active_seconds: toNum(data?.active_seconds, prev.progress?.active_seconds || 0),
                        can_complete: ratio >= toNum(prev?.lesson?.completion_threshold, 95)
                    }
                };
            });
            if (data?.blocked_forward_seek) {
                const allowed = toNum(data?.allowed_position, serverPos);
                setPlayer((p) => ({ ...p, pos: allowed, isPlaying: false }));
            }
        } catch (e) { toastErr(e, 'Heartbeat error'); }
        finally { hbBusy.current = false; }
    }, [api, toastErr]);

    const seekTo = useCallback(async (target) => {
        const duration = toNum(lesson?.lesson?.duration_seconds, 0);
        const next = clamp(toNum(target, 0), 0, duration > 0 ? duration : toNum(target, 0));
        const prev = posRef.current;
        if (Math.abs(next - prev) < 0.1) return;
        await sendLessonEvent('seek', { from_seconds: prev, to_seconds: next });
        if (!mounted.current) return;
        setPlayer((p) => ({ ...p, pos: next, maxSeen: Math.max(p.maxSeen, next), draft: null }));
    }, [lesson?.lesson?.duration_seconds, sendLessonEvent]);

    const completeLesson = useCallback(async () => {
        const id = lessonIdRef.current;
        if (!id) return;
        try {
            await heartbeat();
            await api('POST', `/api/lms/lessons/${id}/complete`, { data: {}, dedupe: false });
            dropLmsCache();
            await Promise.all([loadHome(true), loadCourses(true), course?.id ? openCourse(course.id) : Promise.resolve()]);
            await openLesson(id);
            showToast?.('Урок завершен.', 'success');
        } catch (e) { toastErr(e, 'Complete lesson error'); }
    }, [api, course?.id, dropLmsCache, heartbeat, loadCourses, loadHome, openCourse, openLesson, showToast, toastErr]);

    const saveAnswerNow = useCallback(async (qId, payload) => {
        const attemptId = toNum(attempt?.attempt?.id, 0);
        const id = toNum(qId, 0);
        if (!attemptId || !id) return;
        const ser = json(payload);
        if (lastSaved.current.get(id) === ser) {
            dirty.current.delete(id);
            return;
        }
        setSaveState((p) => ({ ...p, [id]: 'saving' }));
        try {
            await api('PATCH', `/api/lms/tests/attempts/${attemptId}/answer`, {
                data: { question_id: id, answer_payload: payload },
                dedupe: false
            });
            lastSaved.current.set(id, ser);
            dirty.current.delete(id);
            setSaveState((p) => ({ ...p, [id]: 'saved' }));
        } catch (e) {
            setSaveState((p) => ({ ...p, [id]: 'error' }));
            toastErr(e, 'Answer save error');
        }
    }, [api, attempt?.attempt?.id, toastErr]);

    const queueSave = useCallback((qId, payload, immediate = false) => {
        const id = toNum(qId, 0);
        if (!id) return;
        setAnswers((p) => ({ ...p, [id]: payload }));
        dirty.current.add(id);
        const prev = timers.current.get(id);
        if (prev) window.clearTimeout(prev);
        if (immediate) {
            void saveAnswerNow(id, payload);
            return;
        }
        const t = window.setTimeout(() => {
            timers.current.delete(id);
            void saveAnswerNow(id, answersRef.current[id] || payload);
        }, 650);
        timers.current.set(id, t);
    }, [saveAnswerNow]);

    const flushSaves = useCallback(async () => {
        const jobs = [];
        for (const [id, t] of timers.current.entries()) {
            window.clearTimeout(t);
            timers.current.delete(id);
            jobs.push(saveAnswerNow(id, answersRef.current[id] || {}));
        }
        for (const id of Array.from(dirty.current)) {
            if (!timers.current.has(id)) jobs.push(saveAnswerNow(id, answersRef.current[id] || {}));
        }
        await Promise.allSettled(jobs);
    }, [saveAnswerNow]);

    const startTest = useCallback(async (testId) => {
        const id = toNum(testId, 0);
        if (!id) return;
        setLoading((p) => ({ ...p, test: true }));
        setResult(null);
        setAttempt(null);
        setAnswers({});
        setSaveState({});
        timers.current.forEach((t) => window.clearTimeout(t));
        timers.current.clear();
        dirty.current.clear();
        lastSaved.current.clear();
        try {
            const data = await runExclusive('test', (signal) => api('POST', `/api/lms/tests/${id}/start`, { signal, data: {}, dedupe: false }));
            if (!mounted.current) return;
            setAttempt(data || null);
        } catch (e) { toastErr(e, 'Start test error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, test: false })); }
    }, [api, runExclusive, toastErr]);

    const finishTest = useCallback(async () => {
        const id = toNum(attempt?.attempt?.id, 0);
        if (!id) return;
        setLoading((p) => ({ ...p, finish: true }));
        try {
            await flushSaves();
            const fin = await api('POST', `/api/lms/tests/attempts/${id}/finish`, { data: {}, dedupe: false });
            const det = await api('GET', `/api/lms/tests/attempts/${id}/result`, { dedupe: false });
            if (!mounted.current) return;
            setResult({ summary: fin?.result || null, detail: det || null });
            dropLmsCache();
            await Promise.all([loadHome(true), loadCourses(true), loadCerts(true), course?.id ? openCourse(course.id) : Promise.resolve()]);
            showToast?.('Тест завершен.', 'success');
        } catch (e) { toastErr(e, 'Finish test error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, finish: false })); }
    }, [api, attempt?.attempt?.id, course?.id, dropLmsCache, flushSaves, loadCerts, loadCourses, loadHome, openCourse, showToast, toastErr]);

    const markRead = useCallback(async (id) => {
        const nId = toNum(id, 0);
        if (!nId) return;
        setNotes((prev) => prev.map((n) => (n.id === nId ? { ...n, is_read: true, read_at: n.read_at || new Date().toISOString() } : n)));
        try {
            await api('POST', `/api/lms/notifications/${nId}/read`, { data: {}, dedupe: false });
            dropLmsCache();
            await loadHome(true);
        } catch (e) { toastErr(e, 'Read notification error'); }
    }, [api, dropLmsCache, loadHome, toastErr]);

    const downloadCert = useCallback(async (id, number) => {
        const certId = toNum(id, 0);
        if (!certId) return;
        setLoading((p) => ({ ...p, [`dl_${certId}`]: true }));
        try {
            const r = await api('GET', `/api/lms/certificates/${certId}/download`, { responseType: 'blob', dedupe: false });
            const blob = r?.data instanceof Blob ? r.data : new Blob([r?.data], { type: 'application/pdf' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${number || `certificate-${certId}`}.pdf`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
        } catch (e) { toastErr(e, 'Download certificate error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, [`dl_${certId}`]: false })); }
    }, [api, toastErr]);

    const createCourse = useCallback(async () => {
        const title = String(newCourse.title || '').trim();
        if (!title) {
            showToast?.('Введите название курса.', 'info');
            return;
        }
        let modules = [];
        let tests = [];
        if (String(newCourse.blueprint || '').trim()) {
            try {
                const parsed = JSON.parse(newCourse.blueprint);
                modules = Array.isArray(parsed?.modules) ? parsed.modules : [];
                tests = Array.isArray(parsed?.tests) ? parsed.tests : [];
            } catch {
                showToast?.('Некорректный JSON blueprint.', 'error');
                return;
            }
        }
        setLoading((p) => ({ ...p, create_course: true }));
        try {
            await api('POST', '/api/lms/admin/courses', {
                data: { ...newCourse, title, pass_threshold: toNum(newCourse.pass_threshold, 80), attempt_limit: toNum(newCourse.attempt_limit, 3), modules, tests },
                dedupe: false
            });
            dropLmsCache();
            setNewCourse({ title: '', description: '', category: '', pass_threshold: 80, attempt_limit: 3, blueprint: '' });
            await loadAdminCourses(true);
            showToast?.('Курс создан.', 'success');
        } catch (e) { toastErr(e, 'Create course error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, create_course: false })); }
    }, [api, dropLmsCache, loadAdminCourses, newCourse, showToast, toastErr]);

    const publishCourse = useCallback(async (courseId, versionId) => {
        const id = toNum(courseId, 0);
        if (!id) return;
        try {
            await api('POST', `/api/lms/admin/courses/${id}/publish`, { data: versionId ? { course_version_id: versionId } : {}, dedupe: false });
            dropLmsCache();
            await loadAdminCourses(true);
            showToast?.('Курс опубликован.', 'success');
        } catch (e) { toastErr(e, 'Publish course error'); }
    }, [api, dropLmsCache, loadAdminCourses, showToast, toastErr]);

    const assignCourse = useCallback(async () => {
        const courseId = toNum(assign.course_id, 0);
        if (!courseId || !assign.user_ids.length) {
            showToast?.('Выберите курс и пользователей.', 'info');
            return;
        }
        setLoading((p) => ({ ...p, assign: true }));
        try {
            await api('POST', `/api/lms/admin/courses/${courseId}/assignments`, {
                data: { user_ids: assign.user_ids, due_at: assign.due_at || undefined },
                dedupe: false
            });
            dropLmsCache();
            await Promise.all([loadAdminProgress(true), loadAdminCourses(true)]);
            showToast?.('Назначение выполнено.', 'success');
        } catch (e) { toastErr(e, 'Assign error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, assign: false })); }
    }, [api, assign, dropLmsCache, loadAdminCourses, loadAdminProgress, showToast, toastErr]);

    const uploadMaterials = useCallback(async () => {
        if (!upload.files.length) {
            showToast?.('Добавьте файлы.', 'info');
            return;
        }
        const fd = new FormData();
        upload.files.forEach((f) => fd.append('files', f));
        if (upload.lesson_id) fd.append('lesson_id', upload.lesson_id);
        if (upload.title) fd.append('title', upload.title);
        if (upload.material_type) fd.append('material_type', upload.material_type);
        if (upload.position) fd.append('position', upload.position);
        setLoading((p) => ({ ...p, upload: true }));
        try {
            await api('POST', '/api/lms/admin/materials/upload', { data: fd, dedupe: false });
            setUpload({ lesson_id: '', title: '', material_type: 'file', position: '', files: [] });
            showToast?.('Материалы загружены.', 'success');
        } catch (e) { toastErr(e, 'Upload materials error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, upload: false })); }
    }, [api, showToast, toastErr, upload]);

    const revokeCert = useCallback(async () => {
        const id = toNum(revoke.certificate_id, 0);
        if (!id) {
            showToast?.('Введите certificate_id.', 'info');
            return;
        }
        setLoading((p) => ({ ...p, revoke: true }));
        try {
            await api('POST', `/api/lms/admin/certificates/${id}/revoke`, { data: { reason: revoke.reason || 'Revoked by administrator' }, dedupe: false });
            setRevoke({ certificate_id: '', reason: 'Revoked by administrator' });
            showToast?.('Сертификат отозван.', 'success');
        } catch (e) { toastErr(e, 'Revoke certificate error'); }
        finally { if (mounted.current) setLoading((p) => ({ ...p, revoke: false })); }
    }, [api, revoke, showToast, toastErr]);

    useEffect(() => {
        if (isLearner) {
            void loadHome(false);
            void loadCourses(false);
        }
        if (isManager) {
            void loadAdminCourses(false);
        }
    }, [isLearner, isManager, loadAdminCourses, loadCourses, loadHome]);

    useEffect(() => {
        if (tab === 'certs' && isLearner) void loadCerts(false);
        if (tab === 'notes' && isLearner) void loadNotes(false);
        if (tab === 'admin_progress' && isManager) void loadAdminProgress(false);
        if (tab === 'admin_attempts' && isManager) void loadAdminAttempts(false);
        if (tab === 'admin_deadlines' && isManager) void loadAdminProgress(false);
        if (tab === 'admin_assign' && isManager) void loadUsers(false);
    }, [isLearner, isManager, loadAdminAttempts, loadAdminProgress, loadCerts, loadNotes, loadUsers, tab]);

    useEffect(() => {
        if (course?.id || !courses.length || !isLearner) return;
        const firstId = toNum(courses[0]?.course_id, 0);
        if (firstId) void openCourse(firstId);
    }, [course?.id, courses, isLearner, openCourse]);

    useEffect(() => {
        const lessonId = lesson?.lesson?.id;
        if (!lessonId) return undefined;
        const sec = Math.max(5, toNum(lesson?.anti_cheat?.heartbeat_seconds, toNum(home?.heartbeat_seconds, 15)));
        const t = window.setInterval(() => { void heartbeat(); }, sec * 1000);
        return () => window.clearInterval(t);
    }, [heartbeat, home?.heartbeat_seconds, lesson?.anti_cheat?.heartbeat_seconds, lesson?.lesson?.id]);

    useEffect(() => {
        if (!lesson?.lesson?.id || !player.isPlaying) return undefined;
        const t = window.setInterval(() => {
            setPlayer((p) => {
                const duration = toNum(lesson?.lesson?.duration_seconds, 0);
                const next = p.pos + 1;
                const bounded = duration > 0 ? Math.min(next, duration) : next;
                if (duration > 0 && bounded >= duration) return { ...p, pos: duration, maxSeen: Math.max(p.maxSeen, duration), isPlaying: false };
                return { ...p, pos: bounded, maxSeen: Math.max(p.maxSeen, bounded), draft: null };
            });
        }, 1000);
        return () => window.clearInterval(t);
    }, [lesson?.lesson?.duration_seconds, lesson?.lesson?.id, player.isPlaying]);

    useEffect(() => {
        if (!lesson?.lesson?.id) return undefined;
        const fn = () => {
            const vis = document.visibilityState === 'visible';
            visibleRef.current = vis;
            if (!vis) setPlayer((p) => ({ ...p, isPlaying: false }));
            void sendLessonEvent('visibility', { is_visible: vis });
        };
        document.addEventListener('visibilitychange', fn);
        return () => document.removeEventListener('visibilitychange', fn);
    }, [lesson?.lesson?.id, sendLessonEvent]);

    const mergedCourses = useMemo(() => {
        const map = new Map((home?.courses || []).map((c) => [toNum(c.course_id, 0), c]));
        return courses.map((c) => ({ ...c, ...(map.get(toNum(c.course_id, 0)) || {}) }));
    }, [courses, home?.courses]);
    const filteredCourses = useMemo(() => {
        const q = String(courseQuery || '').trim().toLowerCase();
        if (!q) return mergedCourses;
        return mergedCourses.filter((c) => `${c?.title || ''} ${c?.description || ''} ${c?.category || ''}`.toLowerCase().includes(q));
    }, [courseQuery, mergedCourses]);
    const filteredUsers = useMemo(() => {
        const q = String(userQuery || '').trim().toLowerCase();
        if (!q) return users;
        return users.filter((u) => `${u?.name || ''} ${u?.login || ''} ${u?.role || ''}`.toLowerCase().includes(q));
    }, [userQuery, users]);

    const unread = useMemo(() => notes.filter((n) => !n?.is_read).length, [notes]);
    const lessonRatio = toNum(lesson?.progress?.completion_ratio, 0);
    const lessonThreshold = toNum(lesson?.lesson?.completion_threshold, 95);
    const canComplete = lessonRatio >= lessonThreshold || !!lesson?.progress?.can_complete;
    const adminDeadlines = useMemo(() => [...adminProgress].filter((r) => ['on_time', 'late_completed', 'overdue'].includes(r?.deadline_status)).sort((a, b) => new Date(a?.due_at || 0) - new Date(b?.due_at || 0)), [adminProgress]);

    const refresh = useCallback(async () => {
        switch (tab) {
        case 'home': await Promise.all([loadHome(true), loadCourses(true)]); break;
        case 'courses': await Promise.all([loadHome(true), loadCourses(true), course?.id ? openCourse(course.id) : Promise.resolve()]); break;
        case 'certs': await loadCerts(true); break;
        case 'notes': await loadNotes(true); break;
        case 'admin_courses': await loadAdminCourses(true); break;
        case 'admin_assign': await Promise.all([loadAdminCourses(true), loadUsers(true)]); break;
        case 'admin_progress': await loadAdminProgress(true); break;
        case 'admin_attempts': await loadAdminAttempts(true); break;
        case 'admin_deadlines': await loadAdminProgress(true); break;
        default: break;
        }
    }, [course?.id, loadAdminAttempts, loadAdminCourses, loadAdminProgress, loadCerts, loadCourses, loadHome, loadNotes, loadUsers, openCourse, tab]);

    return (
        <div className="space-y-4" style={{ fontFamily: '"Manrope","Segoe UI",sans-serif' }}>
            <section className="relative overflow-hidden rounded-3xl border border-slate-200 bg-gradient-to-br from-slate-950 via-slate-900 to-blue-900 p-5 text-white">
                <div className="absolute inset-0 opacity-60" style={{ backgroundImage: 'radial-gradient(circle at 20% 20%, rgba(56,189,248,.35), transparent 45%), radial-gradient(circle at 80% 65%, rgba(59,130,246,.25), transparent 42%)' }} />
                <div className="relative z-10 flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <div className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs uppercase tracking-[0.15em] text-cyan-200">
                            <ShieldCheck className="h-4 w-4" /> LMS OTP
                        </div>
                        <h2 className="mt-2 text-2xl font-semibold">Раздел обучения</h2>
                        <p className="mt-1 text-sm text-slate-200">Современный интерфейс с server-authoritative anti-cheat и стабильным запросным слоем.</p>
                    </div>
                    <button type="button" onClick={() => { void refresh(); }} className="inline-flex items-center gap-2 rounded-xl border border-white/25 bg-white/10 px-4 py-2 text-sm hover:bg-white/20">
                        <RefreshCw className="h-4 w-4" /> Обновить
                    </button>
                </div>
                {isLearner && (
                    <div className="relative z-10 mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
                        <div className="rounded-xl border border-white/15 bg-white/10 p-3"><div className="text-xs text-cyan-100">Назначено</div><div className="text-xl font-semibold">{toNum(home?.courses?.length, 0)}</div></div>
                        <div className="rounded-xl border border-white/15 bg-white/10 p-3"><div className="text-xs text-cyan-100">В процессе</div><div className="text-xl font-semibold">{toNum((home?.courses || []).filter((c) => c.status === 'in_progress').length, 0)}</div></div>
                        <div className="rounded-xl border border-white/15 bg-white/10 p-3"><div className="text-xs text-cyan-100">Завершено</div><div className="text-xl font-semibold">{toNum((home?.courses || []).filter((c) => c.status === 'completed').length, 0)}</div></div>
                        <div className="rounded-xl border border-white/15 bg-white/10 p-3"><div className="text-xs text-cyan-100">Непрочитанные</div><div className="text-xl font-semibold">{toNum(home?.unread_notifications, unread)}</div></div>
                    </div>
                )}
            </section>

            <div className="rounded-2xl border border-slate-200 bg-white p-2 shadow-sm">
                <div className="flex flex-wrap gap-2">
                    {tabs.map((t) => <Tab key={t.id} icon={t.icon} active={tab === t.id} label={t.label} onClick={() => setTab(t.id)} />)}
                </div>
            </div>

            {isLearner && tab === 'home' && (
                <Card title="Курсы и дедлайны" subtitle="Цветовые статусы дедлайнов: зелёный/оранжевый/красный">
                    {loading.home ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Загрузка...</div>
                    ) : !(home?.courses || []).length ? (
                        <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-sm text-slate-500">Нет назначенных курсов.</div>
                    ) : (
                        <div className="space-y-2">
                            {(home?.courses || []).map((c) => {
                                const status = c?.deadline_status || 'unknown';
                                return (
                                    <button key={c.assignment_id} type="button" onClick={() => { void openCourse(c.course_id); }} className="w-full rounded-xl border border-slate-200 p-3 text-left hover:bg-slate-50">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <div>
                                                <div className="text-sm font-semibold text-slate-900">{c.title}</div>
                                                <div className="text-xs text-slate-500">Дедлайн: {fmtDate(c.due_at)} · Прогресс: {pct(c.progress_percent)}</div>
                                            </div>
                                            <span className={`rounded-full border px-2 py-1 text-xs ${DEADLINE_STYLE[status] || DEADLINE_STYLE.unknown}`}>{DEADLINE_LABEL[status] || DEADLINE_LABEL.unknown}</span>
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                    )}
                </Card>
            )}

            {isLearner && tab === 'courses' && (
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-[340px_1fr]">
                    <Card title="Витрина курсов" subtitle="Назначенные программы">
                        <label className="mb-2 block">
                            <span className="mb-1 inline-flex items-center gap-1 text-xs text-slate-500"><Search className="h-3 w-3" /> Поиск</span>
                            <input value={courseQuery} onChange={(e) => setCourseQuery(e.target.value)} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="Название, категория..." />
                        </label>
                        {loading.courses ? (
                            <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Загрузка...</div>
                        ) : !filteredCourses.length ? (
                            <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-sm text-slate-500">Курсы не найдены.</div>
                        ) : (
                            <div className="space-y-2">
                                {filteredCourses.map((c) => {
                                    const status = c?.deadline_status || 'unknown';
                                    return (
                                        <button key={c.assignment_id || c.course_id} type="button" onClick={() => { void openCourse(c.course_id); }} className={`w-full rounded-xl border p-3 text-left ${course?.id === c.course_id ? 'border-blue-500 bg-blue-50' : 'border-slate-200 hover:bg-slate-50'}`}>
                                            <div className="text-sm font-semibold text-slate-900">{c.title}</div>
                                            <div className="mt-1 text-xs text-slate-500 line-clamp-2">{c.description || 'Без описания'}</div>
                                            <div className="mt-2 h-1.5 rounded-full bg-slate-200">
                                                <div className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-600" style={{ width: `${clamp(toNum(c.progress_percent, 0), 0, 100)}%` }} />
                                            </div>
                                            <div className="mt-1 flex items-center justify-between text-xs">
                                                <span className={`rounded-full border px-2 py-0.5 ${DEADLINE_STYLE[status] || DEADLINE_STYLE.unknown}`}>{DEADLINE_LABEL[status] || DEADLINE_LABEL.unknown}</span>
                                                <span className="text-slate-500">{pct(c.progress_percent)}</span>
                                            </div>
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </Card>

                    <div className="space-y-4">
                        <Card title={course?.title || 'Курс не выбран'} subtitle={course?.description || 'Выберите курс слева'} actions={course?.id ? (
                            <button type="button" onClick={() => { void (async () => { try { await api('POST', `/api/lms/courses/${course.id}/start`, { data: {}, dedupe: false }); dropLmsCache(); await Promise.all([loadHome(true), loadCourses(true)]); showToast?.('Курс запущен.', 'success'); } catch (e) { toastErr(e, 'Start course error'); } })(); }} className="rounded-lg bg-blue-700 px-3 py-1.5 text-sm text-white hover:bg-blue-800">Старт курса</button>
                        ) : null}>
                            {loading.course ? (
                                <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Загрузка структуры...</div>
                            ) : !course ? (
                                <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-sm text-slate-500">Откройте курс для просмотра модулей и тестов.</div>
                            ) : (
                                <div className="space-y-3">
                                    {(course.modules || []).map((m) => (
                                        <div key={m.id} className="rounded-lg border border-slate-200 p-3">
                                            <div className="text-sm font-semibold text-slate-800">{m.title}</div>
                                            <div className="mt-2 space-y-2">
                                                {(m.lessons || []).map((l) => {
                                                    const p = course?.assignment?.lesson_progress?.[l.id] || {};
                                                    return (
                                                        <div key={l.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                                                            <div>
                                                                <div className="text-sm text-slate-800">{l.title}</div>
                                                                <div className="text-xs text-slate-500">{fmtDur(l.duration_seconds)} · {pct(p.completion_ratio || 0)}</div>
                                                            </div>
                                                            <button type="button" onClick={() => { void openLesson(l.id); }} className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs text-white hover:bg-slate-800">Открыть урок</button>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    ))}
                                    <div className="rounded-lg border border-slate-200 p-3">
                                        <div className="mb-2 text-sm font-semibold text-slate-800">Тесты</div>
                                        {(course.tests || []).length ? (course.tests || []).map((t) => (
                                            <div key={t.id} className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                                                <div className="text-sm text-slate-800">{t.title}</div>
                                                <button type="button" onClick={() => { void startTest(t.id); }} className="rounded-lg bg-cyan-700 px-3 py-1.5 text-xs text-white hover:bg-cyan-800">Начать тест</button>
                                            </div>
                                        )) : <div className="text-sm text-slate-500">Тестов пока нет.</div>}
                                    </div>
                                </div>
                            )}
                        </Card>
                        {lesson?.lesson?.id && (
                            <Card title={lesson.lesson.title} subtitle={lesson.lesson.module_title} actions={(
                                <div className="flex items-center gap-2">
                                    <button type="button" onClick={() => setPlayer((p) => ({ ...p, isPlaying: !p.isPlaying }))} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-sm">
                                        {player.isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                                        {player.isPlaying ? 'Пауза' : 'Играть'}
                                    </button>
                                    <button type="button" onClick={() => { void completeLesson(); }} disabled={!canComplete} className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm ${canComplete ? 'bg-emerald-600 text-white' : 'bg-slate-200 text-slate-500'}`}>
                                        <CheckCircle2 className="h-4 w-4" /> Завершить
                                    </button>
                                </div>
                            )}>
                                <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2"><div className="text-xs text-slate-500">Длительность</div><div className="text-sm font-semibold">{fmtDur(lesson.lesson.duration_seconds)}</div></div>
                                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2"><div className="text-xs text-slate-500">Просмотр</div><div className="text-sm font-semibold">{pct(lessonRatio)}</div></div>
                                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2"><div className="text-xs text-slate-500">Heartbeat</div><div className="text-sm font-semibold">{toNum(lesson?.anti_cheat?.heartbeat_seconds, 15)} сек</div></div>
                                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2"><div className="text-xs text-slate-500">Stale gap</div><div className="text-sm font-semibold">{toNum(lesson?.anti_cheat?.stale_gap_seconds, 45)} сек</div></div>
                                </div>
                                <div className="mt-3 rounded-lg border border-slate-200 p-3">
                                    <div className="mb-2 flex items-center justify-between text-sm text-slate-700"><span>Позиция</span><span>{fmtDur(player.pos)} / {fmtDur(lesson.lesson.duration_seconds)}</span></div>
                                    <input type="range" min={0} max={Math.max(1, toNum(lesson.lesson.duration_seconds, 0))} value={player.draft ?? player.pos} onChange={(e) => setPlayer((p) => ({ ...p, draft: toNum(e.target.value, 0) }))} onMouseUp={(e) => { void seekTo(toNum(e.currentTarget.value, 0)); }} onTouchEnd={(e) => { void seekTo(toNum(e.currentTarget.value, 0)); }} className="w-full accent-blue-700" />
                                    <div className="mt-2 flex gap-2">
                                        <button type="button" onClick={() => { void seekTo(player.pos - 10); }} className="rounded-lg border border-slate-300 px-3 py-1 text-xs">-10 сек</button>
                                        <button type="button" onClick={() => { void seekTo(player.pos + 10); }} className="rounded-lg border border-slate-300 px-3 py-1 text-xs">+10 сек</button>
                                        <button type="button" onClick={() => { void heartbeat(); }} className="rounded-lg border border-slate-300 px-3 py-1 text-xs">Sync</button>
                                    </div>
                                </div>
                            </Card>
                        )}
                        {attempt?.attempt?.id && (
                            <Card title={`Тест: попытка #${toNum(attempt.attempt.attempt_no, 1)}`} subtitle={`Порог: ${pct(attempt?.attempt?.pass_threshold)}`} actions={(
                                <button type="button" onClick={() => { void finishTest(); }} disabled={!!loading.finish} className={`rounded-lg px-3 py-1.5 text-sm ${loading.finish ? 'bg-slate-200 text-slate-500' : 'bg-emerald-600 text-white'}`}>
                                    {loading.finish ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Завершить тест'}
                                </button>
                            )}>
                                <div className="space-y-3">
                                    {(attempt.questions || []).map((q, i) => (
                                        <div key={q.id} className="rounded-lg border border-slate-200 p-3">
                                            <div className="mb-2 flex items-center justify-between gap-2">
                                                <div className="text-sm font-semibold text-slate-900">{i + 1}. {q.prompt}</div>
                                                <div className="text-xs text-slate-500">{String(q.type || '').toUpperCase()}</div>
                                            </div>
                                            <QBlock q={q} value={answers[q.id] || {}} state={saveState[q.id]} onChange={(payload, immediate) => queueSave(q.id, payload, immediate)} />
                                            <div className="mt-2 text-xs">
                                                {saveState[q.id] === 'saving' ? <span className="text-blue-600">Сохраняем...</span> : null}
                                                {saveState[q.id] === 'saved' ? <span className="text-emerald-600">Сохранено</span> : null}
                                                {saveState[q.id] === 'error' ? <span className="text-rose-600">Ошибка сохранения</span> : null}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </Card>
                        )}
                        {result?.summary && (
                            <Card title="Результат теста" subtitle="Серверная оценка">
                                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3"><div className="text-xs text-slate-500">Оценка</div><div className="text-xl font-semibold">{pct(result.summary.score_percent)}</div></div>
                                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3"><div className="text-xs text-slate-500">Порог</div><div className="text-xl font-semibold">{pct(result.summary.pass_threshold)}</div></div>
                                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3"><div className="text-xs text-slate-500">Статус</div><div className={`text-xl font-semibold ${result.summary.passed ? 'text-emerald-600' : 'text-rose-600'}`}>{result.summary.passed ? 'Пройден' : 'Не пройден'}</div></div>
                                </div>
                            </Card>
                        )}
                    </div>
                </div>
            )}
            {isLearner && tab === 'certs' && (
                <Card title="Сертификаты" subtitle="PDF с verify токеном">
                    {loading.certs ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Загрузка...</div>
                    ) : !certs.length ? (
                        <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-sm text-slate-500">Сертификаты отсутствуют.</div>
                    ) : (
                        <div className="space-y-2">
                            {certs.map((c) => (
                                <div key={c.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 p-3">
                                    <div>
                                        <div className="text-sm font-semibold text-slate-900">{c.certificate_number}</div>
                                        <div className="text-xs text-slate-500">Статус: {c.status} · Выдан: {fmtDate(c.issued_at)}</div>
                                    </div>
                                    <button type="button" onClick={() => { void downloadCert(c.id, c.certificate_number); }} className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-800">
                                        {loading[`dl_${c.id}`] ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                                        Скачать
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}
                </Card>
            )}
            {isLearner && tab === 'notes' && (
                <Card title="Уведомления" subtitle="События LMS">
                    {loading.notes ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Загрузка...</div>
                    ) : !notes.length ? (
                        <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-sm text-slate-500">Уведомлений нет.</div>
                    ) : (
                        <div className="space-y-2">
                            {notes.map((n) => (
                                <div key={n.id} className={`rounded-xl border p-3 ${n.is_read ? 'border-slate-200 bg-white' : 'border-cyan-200 bg-cyan-50'}`}>
                                    <div className="flex flex-wrap items-start justify-between gap-2">
                                        <div>
                                            <div className="text-sm font-semibold text-slate-900">{n.title}</div>
                                            <div className="text-xs text-slate-500">{n.message || 'Без текста'}</div>
                                            <div className="mt-1 text-[11px] text-slate-400">{fmtDate(n.created_at)}</div>
                                        </div>
                                        {!n.is_read ? <button type="button" onClick={() => { void markRead(n.id); }} className="rounded-lg bg-slate-900 px-2.5 py-1.5 text-xs text-white">Прочитано</button> : <span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">Прочитано</span>}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </Card>
            )}
            {isManager && tab === 'admin_courses' && (
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-[0.9fr_1.1fr]">
                    <Card title="Создание курса" subtitle="MVP-конструктор">
                        <div className="space-y-2">
                            <input value={newCourse.title} onChange={(e) => setNewCourse((p) => ({ ...p, title: e.target.value }))} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="Название" />
                            <textarea value={newCourse.description} onChange={(e) => setNewCourse((p) => ({ ...p, description: e.target.value }))} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" rows={3} placeholder="Описание" />
                            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                                <input value={newCourse.category} onChange={(e) => setNewCourse((p) => ({ ...p, category: e.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="Категория" />
                                <input type="number" value={newCourse.pass_threshold} onChange={(e) => setNewCourse((p) => ({ ...p, pass_threshold: e.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="Порог" />
                                <input type="number" value={newCourse.attempt_limit} onChange={(e) => setNewCourse((p) => ({ ...p, attempt_limit: e.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="Попытки" />
                            </div>
                            <textarea value={newCourse.blueprint} onChange={(e) => setNewCourse((p) => ({ ...p, blueprint: e.target.value }))} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-xs font-mono" rows={5} placeholder='Опц. JSON {"modules":[],"tests":[]}' />
                            <button type="button" onClick={() => { void createCourse(); }} className="inline-flex items-center gap-2 rounded-lg bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800">{loading.create_course ? <Loader2 className="h-4 w-4 animate-spin" /> : <GraduationCap className="h-4 w-4" />} Создать</button>
                        </div>
                    </Card>
                    <Card title="Каталог курсов" subtitle="Управление публикацией">
                        {loading.admin_courses ? <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Загрузка...</div> : (
                            <div className="space-y-2">
                                {adminCourses.map((c) => (
                                    <div key={c.id} className="rounded-xl border border-slate-200 p-3">
                                        <div className="flex flex-wrap items-start justify-between gap-2">
                                            <div>
                                                <div className="text-sm font-semibold text-slate-900">{c.title}</div>
                                                <div className="text-xs text-slate-500">Статус: {c.status} · Версия: {c.current_version?.version_number || '—'}</div>
                                            </div>
                                            <button type="button" onClick={() => { void publishCourse(c.id, c.current_version_id); }} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-emerald-700">Publish</button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </Card>
                </div>
            )}
            {isManager && tab === 'admin_assign' && (
                <Card title="Назначения" subtitle="Назначайте курс операторам/trainee">
                    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[280px_1fr]">
                        <div className="space-y-2">
                            <select value={assign.course_id} onChange={(e) => setAssign((p) => ({ ...p, course_id: e.target.value }))} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
                                <option value="">Выберите курс</option>
                                {adminCourses.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
                            </select>
                            <input type="datetime-local" value={assign.due_at} onChange={(e) => setAssign((p) => ({ ...p, due_at: e.target.value }))} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                            <button type="button" onClick={() => { void assignCourse(); }} className="w-full rounded-lg bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800">{loading.assign ? 'Назначаем...' : `Назначить (${assign.user_ids.length})`}</button>
                        </div>
                        <div className="rounded-lg border border-slate-200 p-3">
                            <label className="mb-2 block">
                                <span className="mb-1 inline-flex items-center gap-1 text-xs text-slate-500"><Search className="h-3 w-3" /> Поиск сотрудника</span>
                                <input value={userQuery} onChange={(e) => setUserQuery(e.target.value)} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="Имя, логин..." />
                            </label>
                            <div className="max-h-80 space-y-2 overflow-auto">
                                {filteredUsers.map((u) => (
                                    <label key={u.id} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 text-sm">
                                        <input type="checkbox" checked={assign.user_ids.includes(u.id)} onChange={(e) => setAssign((p) => ({ ...p, user_ids: e.target.checked ? Array.from(new Set([...p.user_ids, u.id])) : p.user_ids.filter((id) => id !== u.id) }))} />
                                        <span className="font-medium text-slate-800">{u.name}</span>
                                        <span className="text-xs text-slate-500">{u.role}</span>
                                    </label>
                                ))}
                            </div>
                        </div>
                    </div>
                </Card>
            )}
            {isManager && tab === 'admin_progress' && (
                <Card title="Прогресс" subtitle="Статусы обучения сотрудников">
                    {loading.admin_progress ? <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Загрузка...</div> : (
                        <div className="space-y-2">
                            {adminProgress.map((r) => {
                                const s = r?.deadline_status || 'unknown';
                                return (
                                    <div key={r.assignment_id} className="rounded-xl border border-slate-200 p-3">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <div>
                                                <div className="text-sm font-semibold text-slate-900">{r.user_name} · {r.course_title}</div>
                                                <div className="text-xs text-slate-500">{pct(r.progress_percent)} · {toNum(r.completed_lessons, 0)}/{toNum(r.total_lessons, 0)} уроков · дедлайн {fmtDate(r.due_at)}</div>
                                            </div>
                                            <span className={`rounded-full border px-2 py-1 text-xs ${DEADLINE_STYLE[s] || DEADLINE_STYLE.unknown}`}>{DEADLINE_LABEL[s] || DEADLINE_LABEL.unknown}</span>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </Card>
            )}
            {isManager && tab === 'admin_attempts' && (
                <Card title="Попытки тестов" subtitle="История прохождений">
                    {loading.admin_attempts ? <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Загрузка...</div> : (
                        <div className="space-y-2">
                            {adminAttempts.map((a) => (
                                <div key={a.attempt_id} className="rounded-xl border border-slate-200 p-3">
                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                        <div>
                                            <div className="text-sm font-semibold text-slate-900">{a.user_name} · {a.course_title}</div>
                                            <div className="text-xs text-slate-500">{a.test_title} · попытка #{toNum(a.attempt_no, 1)} · {fmtDate(a.finished_at || a.started_at)}</div>
                                        </div>
                                        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs ${a.passed ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
                                            {a.passed ? <Check className="h-3.5 w-3.5" /> : <X className="h-3.5 w-3.5" />}
                                            {a.score_percent !== null ? `${a.score_percent}%` : a.status}
                                        </span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </Card>
            )}
            {isManager && tab === 'admin_deadlines' && (
                <Card title="Дедлайны" subtitle="Цветовая индикация статусов">
                    {loading.admin_progress ? <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Загрузка...</div> : (
                        <div className="space-y-2">
                            {adminDeadlines.map((r) => {
                                const s = r?.deadline_status || 'unknown';
                                return (
                                    <div key={`d-${r.assignment_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 p-3">
                                        <div>
                                            <div className="text-sm font-semibold text-slate-900">{r.user_name} · {r.course_title}</div>
                                            <div className="text-xs text-slate-500">Дедлайн: {fmtDate(r.due_at)} · завершено: {fmtDate(r.completed_at)}</div>
                                        </div>
                                        <span className={`rounded-full border px-2 py-1 text-xs ${DEADLINE_STYLE[s] || DEADLINE_STYLE.unknown}`}>{DEADLINE_LABEL[s] || DEADLINE_LABEL.unknown}</span>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </Card>
            )}
            {isManager && tab === 'admin_materials' && (
                <Card title="Загрузка материалов" subtitle="GCS upload в LMS">
                    <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                        <input value={upload.lesson_id} onChange={(e) => setUpload((p) => ({ ...p, lesson_id: e.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="lesson_id (опц.)" />
                        <input value={upload.title} onChange={(e) => setUpload((p) => ({ ...p, title: e.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="title" />
                        <select value={upload.material_type} onChange={(e) => setUpload((p) => ({ ...p, material_type: e.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
                            <option value="file">file</option><option value="video">video</option><option value="pdf">pdf</option><option value="link">link</option><option value="text">text</option>
                        </select>
                        <input value={upload.position} onChange={(e) => setUpload((p) => ({ ...p, position: e.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="position" />
                        <input type="file" multiple onChange={(e) => setUpload((p) => ({ ...p, files: Array.from(e.target.files || []) }))} className="md:col-span-2 rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                    </div>
                    <div className="mt-3 flex items-center justify-between">
                        <div className="text-xs text-slate-500">Файлов: {upload.files.length}</div>
                        <button type="button" onClick={() => { void uploadMaterials(); }} className="rounded-lg bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800">{loading.upload ? 'Загрузка...' : 'Upload'}</button>
                    </div>
                </Card>
            )}
            {isManager && isFullAdmin && tab === 'admin_revoke' && (
                <Card title="Отзыв сертификата" subtitle="Только admin/super_admin">
                    <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                        <input value={revoke.certificate_id} onChange={(e) => setRevoke((p) => ({ ...p, certificate_id: e.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="certificate_id" />
                        <input value={revoke.reason} onChange={(e) => setRevoke((p) => ({ ...p, reason: e.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="reason" />
                    </div>
                    <button type="button" onClick={() => { void revokeCert(); }} className="mt-3 rounded-lg bg-rose-600 px-4 py-2 text-sm text-white hover:bg-rose-700">{loading.revoke ? 'Отзыв...' : 'Отозвать'}</button>
                </Card>
            )}
            {!tabs.length && (
                <Card title="LMS недоступен">
                    <div className="text-sm text-slate-500">Для вашей роли раздел обучения недоступен.</div>
                </Card>
            )}
        </div>
    );
};

export default LmsView;
