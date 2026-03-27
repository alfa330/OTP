import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';

const LEARNER = new Set(['operator', 'trainee']);
const MANAGER = new Set(['sv', 'trainer', 'admin', 'super_admin']);
const ADMIN = new Set(['admin', 'super_admin']);

const LmsView = ({ user, apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const role = String(user?.role || '').toLowerCase();
    const isLearner = LEARNER.has(role);
    const isManager = MANAGER.has(role);
    const isAdmin = ADMIN.has(role);

    const [tab, setTab] = useState(isLearner ? 'home' : 'admin_courses');
    const [home, setHome] = useState({});
    const [courses, setCourses] = useState([]);
    const [course, setCourse] = useState(null);
    const [lesson, setLesson] = useState(null);
    const [pos, setPos] = useState(0);
    const [attempt, setAttempt] = useState(null);
    const [answers, setAnswers] = useState({});
    const [result, setResult] = useState(null);
    const [certs, setCerts] = useState([]);
    const [notes, setNotes] = useState([]);

    const [adminCourses, setAdminCourses] = useState([]);
    const [adminProgress, setAdminProgress] = useState([]);
    const [adminAttempts, setAdminAttempts] = useState([]);
    const [assignUsers, setAssignUsers] = useState([]);
    const [newCourse, setNewCourse] = useState({ title: '', description: '', category: '', pass_threshold: 80, attempt_limit: 3 });
    const [assign, setAssign] = useState({ course_id: '', due_at: '', user_ids: [] });
    const [upload, setUpload] = useState({ lesson_id: '', files: [] });

    const headers = useMemo(() => withAccessTokenHeader({
        'X-API-Key': user?.apiKey,
        'X-User-Id': user?.id
    }), [withAccessTokenHeader, user?.apiKey, user?.id]);

    const req = useCallback((method, path, data = null, config = {}) => {
        const cfg = {
            method,
            url: `${apiBaseUrl}${path}`,
            headers: { ...headers, ...(config.headers || {}) },
            ...config
        };
        if (data !== null) cfg.data = data;
        return axios(cfg);
    }, [apiBaseUrl, headers]);

    const reloadLearner = useCallback(async () => {
        if (!isLearner) return;
        try {
            const [h, c, s, n] = await Promise.all([
                req('get', '/api/lms/home'),
                req('get', '/api/lms/courses'),
                req('get', '/api/lms/certificates'),
                req('get', '/api/lms/notifications')
            ]);
            setHome(h.data || {});
            setCourses(c.data?.courses || []);
            setCerts(s.data?.certificates || []);
            setNotes(n.data?.notifications || []);
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'LMS learner load failed', 'error');
        }
    }, [isLearner, req, showToast]);

    const reloadManager = useCallback(async () => {
        if (!isManager) return;
        try {
            const [c, p, a, u] = await Promise.all([
                req('get', '/api/lms/admin/courses'),
                req('get', '/api/lms/admin/progress'),
                req('get', '/api/lms/admin/attempts'),
                req('get', '/api/admin/users')
            ]);
            setAdminCourses(c.data?.courses || []);
            setAdminProgress(p.data?.rows || []);
            setAdminAttempts(a.data?.attempts || []);
            setAssignUsers((u.data?.users || []).filter((x) => ['operator', 'trainee'].includes(String(x?.role || '').toLowerCase())));
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'LMS manager load failed', 'error');
        }
    }, [isManager, req, showToast]);

    useEffect(() => {
        if (isLearner) reloadLearner();
        if (isManager) reloadManager();
    }, [isLearner, isManager, reloadLearner, reloadManager]);

    useEffect(() => {
        if (!lesson?.lesson?.id) return undefined;
        const id = lesson.lesson.id;
        const tick = async () => {
            try {
                await req('post', `/api/lms/lessons/${id}/heartbeat`, {
                    position_seconds: pos,
                    tab_visible: document.visibilityState === 'visible'
                });
            } catch {
                // silent heartbeat failures
            }
        };
        tick();
        const timer = window.setInterval(tick, 15000);
        return () => window.clearInterval(timer);
    }, [lesson?.lesson?.id, pos, req]);

    const openCourse = async (id) => {
        try {
            const r = await req('get', `/api/lms/courses/${id}`);
            setCourse(r.data?.course || null);
            setLesson(null);
            setAttempt(null);
            setResult(null);
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Open course failed', 'error');
        }
    };

    const openLesson = async (id) => {
        try {
            const r = await req('get', `/api/lms/lessons/${id}`);
            setLesson(r.data || null);
            setPos(Number(r.data?.progress?.max_position_seconds || 0));
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Open lesson failed', 'error');
        }
    };

    const completeLesson = async () => {
        const id = lesson?.lesson?.id;
        if (!id) return;
        try {
            await req('post', `/api/lms/lessons/${id}/complete`, {});
            showToast?.('Lesson completed', 'success');
            if (course?.id) await openCourse(course.id);
            await reloadLearner();
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Complete lesson failed', 'error');
        }
    };

    const startTest = async (id) => {
        try {
            const r = await req('post', `/api/lms/tests/${id}/start`, {});
            setAttempt(r.data || null);
            setAnswers({});
            setResult(null);
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Start test failed', 'error');
        }
    };

    const saveAnswer = async (questionId, payload) => {
        const attemptId = attempt?.attempt?.id;
        if (!attemptId) return;
        setAnswers((prev) => ({ ...prev, [questionId]: payload }));
        try {
            await req('patch', `/api/lms/tests/attempts/${attemptId}/answer`, { question_id: questionId, answer_payload: payload });
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Save answer failed', 'error');
        }
    };

    const finishTest = async () => {
        const attemptId = attempt?.attempt?.id;
        if (!attemptId) return;
        try {
            const r = await req('post', `/api/lms/tests/attempts/${attemptId}/finish`, {});
            setResult(r.data?.result || null);
            await reloadLearner();
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Finish test failed', 'error');
        }
    };

    const createCourse = async () => {
        try {
            await req('post', '/api/lms/admin/courses', { ...newCourse, pass_threshold: Number(newCourse.pass_threshold), attempt_limit: Number(newCourse.attempt_limit) });
            setNewCourse({ title: '', description: '', category: '', pass_threshold: 80, attempt_limit: 3 });
            await reloadManager();
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Create course failed', 'error');
        }
    };

    const publishCourse = async (courseId, versionId) => {
        try {
            await req('post', `/api/lms/admin/courses/${courseId}/publish`, { course_version_id: versionId });
            await reloadManager();
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Publish failed', 'error');
        }
    };

    const assignCourse = async () => {
        const courseId = Number(assign.course_id);
        if (!courseId) return;
        try {
            await req('post', `/api/lms/admin/courses/${courseId}/assignments`, {
                user_ids: assign.user_ids,
                due_at: assign.due_at || undefined
            });
            await reloadManager();
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Assign failed', 'error');
        }
    };

    const uploadMaterials = async () => {
        if (!upload.files.length) return;
        const form = new FormData();
        upload.files.forEach((f) => form.append('files', f));
        if (upload.lesson_id) form.append('lesson_id', upload.lesson_id);
        try {
            await req('post', '/api/lms/admin/materials/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } });
            setUpload({ lesson_id: '', files: [] });
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Upload failed', 'error');
        }
    };

    return (
        <div className="space-y-4">
            <div className="bg-white border rounded-lg p-3 flex flex-wrap gap-2">
                {isLearner && ['home', 'courses', 'certs', 'notes'].map((t) => <button key={t} onClick={() => setTab(t)} className={`px-3 py-1 rounded text-sm ${tab === t ? 'bg-blue-600 text-white' : 'bg-gray-100'}`}>{t}</button>)}
                {isManager && ['admin_courses', 'admin_assign', 'admin_progress', 'admin_attempts', 'admin_materials'].map((t) => <button key={t} onClick={() => setTab(t)} className={`px-3 py-1 rounded text-sm ${tab === t ? 'bg-blue-600 text-white' : 'bg-gray-100'}`}>{t}</button>)}
                <button className="ml-auto px-3 py-1 rounded text-sm bg-black text-white" onClick={() => { if (isLearner) reloadLearner(); if (isManager) reloadManager(); }}>refresh</button>
            </div>

            {isLearner && tab === 'home' && <pre className="bg-white border rounded-lg p-3 text-xs overflow-auto">{JSON.stringify(home, null, 2)}</pre>}

            {isLearner && tab === 'courses' && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                    <div className="bg-white border rounded-lg p-3 space-y-2">
                        {(courses || []).map((c) => (
                            <div key={c.assignment_id} className="border rounded p-2">
                                <div className="text-sm font-semibold">{c.title}</div>
                                <div className="flex gap-2 mt-2">
                                    <button className="px-2 py-1 text-xs rounded bg-blue-600 text-white" onClick={() => openCourse(c.course_id)}>open</button>
                                    <button className="px-2 py-1 text-xs rounded bg-gray-900 text-white" onClick={() => req('post', `/api/lms/courses/${c.course_id}/start`, {}).then(() => reloadLearner())}>start</button>
                                </div>
                            </div>
                        ))}
                    </div>
                    <div className="lg:col-span-2 bg-white border rounded-lg p-3 space-y-3">
                        {course && (
                            <>
                                <div className="font-semibold">{course.title}</div>
                                {(course.modules || []).map((m) => (
                                    <div key={m.id} className="border rounded p-2">
                                        <div className="text-sm font-medium">{m.title}</div>
                                        {(m.lessons || []).map((l) => <button key={l.id} onClick={() => openLesson(l.id)} className="mr-2 mt-2 px-2 py-1 text-xs rounded bg-indigo-600 text-white">{l.title}</button>)}
                                    </div>
                                ))}
                                <div className="border rounded p-2">
                                    {(course.tests || []).map((t) => <button key={t.id} onClick={() => startTest(t.id)} className="mr-2 mt-2 px-2 py-1 text-xs rounded bg-emerald-600 text-white">{t.title}</button>)}
                                </div>
                            </>
                        )}
                        {lesson?.lesson && (
                            <div className="border rounded p-2 space-y-2">
                                <div className="font-medium">{lesson.lesson.title}</div>
                                <input type="number" className="px-2 py-1 border rounded text-sm" value={pos} onChange={(e) => setPos(Number(e.target.value || 0))} />
                                <button className="px-2 py-1 text-xs rounded bg-green-600 text-white" onClick={completeLesson}>complete lesson</button>
                            </div>
                        )}
                        {attempt?.attempt && (
                            <div className="border rounded p-2 space-y-2">
                                <div className="font-medium">attempt #{attempt.attempt.attempt_no}</div>
                                {(attempt.questions || []).map((q) => (
                                    <div key={q.id} className="border rounded p-2">
                                        <div className="text-sm">{q.prompt}</div>
                                        {q.type === 'text' && <textarea className="w-full border rounded p-1 text-xs mt-1" onChange={(e) => saveAnswer(q.id, { text: e.target.value })} />}
                                        {q.type !== 'text' && (q.options || []).map((o) => (
                                            <button key={o.id} className="mr-2 mt-1 px-2 py-1 text-xs rounded bg-gray-200" onClick={() => saveAnswer(q.id, { option_id: o.id })}>{o.text}</button>
                                        ))}
                                    </div>
                                ))}
                                <button className="px-2 py-1 text-xs rounded bg-emerald-700 text-white" onClick={finishTest}>finish test</button>
                            </div>
                        )}
                        {result && <pre className="bg-gray-50 border rounded p-2 text-xs overflow-auto">{JSON.stringify(result, null, 2)}</pre>}
                    </div>
                </div>
            )}

            {isLearner && tab === 'certs' && <pre className="bg-white border rounded-lg p-3 text-xs overflow-auto">{JSON.stringify(certs, null, 2)}</pre>}
            {isLearner && tab === 'notes' && <pre className="bg-white border rounded-lg p-3 text-xs overflow-auto">{JSON.stringify(notes, null, 2)}</pre>}

            {isManager && tab === 'admin_courses' && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div className="bg-white border rounded-lg p-3 space-y-2">
                        <input className="w-full border rounded p-2 text-sm" placeholder="title" value={newCourse.title} onChange={(e) => setNewCourse((p) => ({ ...p, title: e.target.value }))} />
                        <textarea className="w-full border rounded p-2 text-sm" placeholder="description" value={newCourse.description} onChange={(e) => setNewCourse((p) => ({ ...p, description: e.target.value }))} />
                        <button className="px-3 py-1 rounded bg-blue-600 text-white text-sm" onClick={createCourse}>create</button>
                    </div>
                    <div className="bg-white border rounded-lg p-3 space-y-2">
                        {(adminCourses || []).map((c) => (
                            <div key={c.id} className="border rounded p-2">
                                <div className="text-sm font-semibold">{c.title}</div>
                                <button className="mt-1 px-2 py-1 text-xs rounded bg-emerald-600 text-white" onClick={() => publishCourse(c.id, c.current_version_id)}>publish</button>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {isManager && tab === 'admin_assign' && (
                <div className="bg-white border rounded-lg p-3 space-y-2">
                    <select className="w-full border rounded p-2 text-sm" value={assign.course_id} onChange={(e) => setAssign((p) => ({ ...p, course_id: e.target.value }))}>
                        <option value="">course</option>
                        {adminCourses.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
                    </select>
                    <input type="datetime-local" className="w-full border rounded p-2 text-sm" value={assign.due_at} onChange={(e) => setAssign((p) => ({ ...p, due_at: e.target.value }))} />
                    <div className="border rounded p-2 max-h-40 overflow-auto">
                        {assignUsers.map((u) => (
                            <label key={u.id} className="block text-sm">
                                <input type="checkbox" checked={assign.user_ids.includes(u.id)} onChange={(e) => setAssign((p) => ({ ...p, user_ids: e.target.checked ? [...p.user_ids, u.id] : p.user_ids.filter((id) => id !== u.id) }))} /> {u.name} ({u.role})
                            </label>
                        ))}
                    </div>
                    <button className="px-3 py-1 rounded bg-blue-600 text-white text-sm" onClick={assignCourse}>assign</button>
                </div>
            )}

            {isManager && tab === 'admin_progress' && <pre className="bg-white border rounded-lg p-3 text-xs overflow-auto">{JSON.stringify(adminProgress, null, 2)}</pre>}
            {isManager && tab === 'admin_attempts' && <pre className="bg-white border rounded-lg p-3 text-xs overflow-auto">{JSON.stringify(adminAttempts, null, 2)}</pre>}

            {isManager && tab === 'admin_materials' && (
                <div className="bg-white border rounded-lg p-3 space-y-2">
                    <input className="w-full border rounded p-2 text-sm" placeholder="lesson_id (optional)" value={upload.lesson_id} onChange={(e) => setUpload((p) => ({ ...p, lesson_id: e.target.value }))} />
                    <input type="file" multiple onChange={(e) => setUpload((p) => ({ ...p, files: Array.from(e.target.files || []) }))} />
                    <button className="px-3 py-1 rounded bg-blue-600 text-white text-sm" onClick={uploadMaterials}>upload</button>
                </div>
            )}

            {isAdmin && certs.some((c) => c.status === 'active') && (
                <div className="bg-white border rounded-lg p-3 text-xs text-gray-600">
                    revoke certificate is available from admin API endpoint: <code>/api/lms/admin/certificates/&lt;id&gt;/revoke</code>
                </div>
            )}
        </div>
    );
};

export default LmsView;
