import React, { useEffect, useMemo, useState } from 'react';

const STORAGE_KEY = 'otp_surveys_v1';

const TENURE_BUCKETS = [
    { id: 'lt3', label: 'До 3 месяцев' },
    { id: 'm3_6', label: '3-6 месяцев' },
    { id: 'm7_12', label: '7-12 месяцев' },
    { id: 'gt12', label: 'Больше 12 месяцев' },
    { id: 'unknown', label: 'Стаж не указан' }
];

const QUESTION_TYPES = [
    { value: 'single', label: 'Один вариант' },
    { value: 'multiple', label: 'Несколько вариантов' },
    { value: 'rating', label: 'Рейтинг 1-5 звезд' }
];

const buildId = (prefix = 'id') => `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

const emptyQuestion = () => ({ id: buildId('q'), text: '', type: 'single', required: true, allowOther: false, options: ['', ''] });
const emptyDraft = () => ({ title: '', description: '', directionIds: [], tenureBuckets: [], operatorIds: [], questions: [emptyQuestion()] });

const tenureMonths = (dateLike) => {
    if (!dateLike) return null;
    const d = new Date(dateLike);
    if (Number.isNaN(d.getTime())) return null;
    const now = new Date();
    let m = (now.getFullYear() - d.getFullYear()) * 12 + (now.getMonth() - d.getMonth());
    if (now.getDate() < d.getDate()) m -= 1;
    return Math.max(0, m);
};

const tenureBucket = (m) => {
    if (!Number.isFinite(m)) return 'unknown';
    if (m < 3) return 'lt3';
    if (m <= 6) return 'm3_6';
    if (m <= 12) return 'm7_12';
    return 'gt12';
};

const tenureLabel = (m) => {
    if (!Number.isFinite(m)) return 'Стаж не указан';
    if (m === 0) return 'Меньше месяца';
    if (m < 12) return `${m} мес.`;
    const y = Math.floor(m / 12);
    const r = m % 12;
    return r ? `${y} г. ${r} мес.` : `${y} г.`;
};

const SurveysView = ({ user, operators = [], directions = [], showToast }) => {
    const [surveys, setSurveys] = useState(() => {
        try {
            const raw = window.localStorage.getItem(STORAGE_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch {
            return [];
        }
    });
    const [draft, setDraft] = useState(emptyDraft);
    const [showBuilder, setShowBuilder] = useState(false);
    const [selectedSurveyId, setSelectedSurveyId] = useState('');
    const [operatorQuery, setOperatorQuery] = useState('');

    useEffect(() => {
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(surveys));
        } catch {
            // ignore storage write errors
        }
    }, [surveys]);

    useEffect(() => {
        if (!selectedSurveyId && surveys[0]?.id) setSelectedSurveyId(surveys[0].id);
        if (selectedSurveyId && !surveys.some((s) => s.id === selectedSurveyId)) setSelectedSurveyId(surveys[0]?.id || '');
    }, [selectedSurveyId, surveys]);

    const notify = (message, type = 'success') => {
        if (typeof showToast === 'function') showToast(message, type);
    };

    const directionNameById = useMemo(() => {
        const map = new Map();
        (directions || []).forEach((d) => {
            const id = d?.id != null ? String(d.id) : null;
            const name = d?.name || d?.title || d?.displayName || d?.direction_name || d?.direction || 'Без направления';
            if (id) map.set(id, name);
        });
        return map;
    }, [directions]);

    const directionOptions = useMemo(
        () => Array.from(directionNameById.entries()).map(([id, name]) => ({ id, name })),
        [directionNameById]
    );

    const normalizedOperators = useMemo(() => {
        return (operators || [])
            .map((op) => {
                const id = Number(op?.id);
                if (!Number.isFinite(id)) return null;
                const m = tenureMonths(op?.hire_date);
                const directionId = op?.direction_id != null ? String(op.direction_id) : 'none';
                return {
                    id,
                    name: String(op?.name || op?.login || `#${id}`),
                    directionId,
                    directionName: op?.direction || op?.direction_name || directionNameById.get(directionId) || 'Без направления',
                    tenureBucket: tenureBucket(m),
                    tenureLabel: tenureLabel(m)
                };
            })
            .filter(Boolean)
            .sort((a, b) => a.name.localeCompare(b.name, 'ru', { sensitivity: 'base' }));
    }, [operators, directionNameById]);

    const filteredOperators = useMemo(() => {
        const q = operatorQuery.trim().toLowerCase();
        const directionSet = new Set(draft.directionIds.map(String));
        const tenureSet = new Set(draft.tenureBuckets);
        return normalizedOperators.filter((op) => {
            const byDirection = directionSet.size === 0 || directionSet.has(op.directionId);
            const byTenure = tenureSet.size === 0 || tenureSet.has(op.tenureBucket);
            const byQuery = !q || op.name.toLowerCase().includes(q) || op.directionName.toLowerCase().includes(q) || op.tenureLabel.toLowerCase().includes(q);
            return byDirection && byTenure && byQuery;
        });
    }, [draft.directionIds, draft.tenureBuckets, normalizedOperators, operatorQuery]);

    const selectedSurvey = useMemo(() => surveys.find((s) => s.id === selectedSurveyId) || null, [selectedSurveyId, surveys]);
    const selectedSurveyOperators = useMemo(() => {
        if (!selectedSurvey) return [];
        const ids = new Set((selectedSurvey.assignment?.operatorIds || []).map(Number));
        return normalizedOperators.filter((op) => ids.has(op.id));
    }, [normalizedOperators, selectedSurvey]);

    const toggleArrayValue = (setter, key, value) => {
        setter((prev) => {
            const set = new Set(prev[key] || []);
            if (set.has(value)) set.delete(value);
            else set.add(value);
            return { ...prev, [key]: Array.from(set) };
        });
    };

    const updateQuestion = (id, patch) => {
        setDraft((prev) => ({ ...prev, questions: prev.questions.map((q) => (q.id === id ? { ...q, ...patch } : q)) }));
    };

    const validate = () => {
        if (!draft.title.trim()) return 'Укажите название опроса';
        if (!draft.operatorIds.length) return 'Выберите минимум одного оператора';
        for (let i = 0; i < draft.questions.length; i += 1) {
            const q = draft.questions[i];
            if (!q.text.trim()) return `Заполните текст вопроса #${i + 1}`;
            if (q.type !== 'rating') {
                const options = (q.options || []).map((o) => String(o || '').trim()).filter(Boolean);
                if (options.length < 2) return `Нужно минимум 2 варианта в вопросе #${i + 1}`;
            }
        }
        return '';
    };

    const createSurvey = () => {
        const error = validate();
        if (error) return notify(error, 'error');
        const payload = {
            id: buildId('survey'),
            title: draft.title.trim(),
            description: draft.description.trim(),
            createdAt: new Date().toISOString(),
            createdBy: { id: user?.id || null, name: user?.name || 'Система', role: user?.role || 'unknown' },
            assignment: {
                directionIds: [...draft.directionIds],
                tenureBuckets: [...draft.tenureBuckets],
                operatorIds: [...draft.operatorIds]
            },
            questions: draft.questions.map((q) => ({
                id: buildId('question'),
                text: q.text.trim(),
                type: q.type,
                required: !!q.required,
                allowOther: q.type === 'rating' ? false : !!q.allowOther,
                options: q.type === 'rating' ? [] : (q.options || []).map((o) => String(o || '').trim()).filter(Boolean)
            }))
        };
        setSurveys((prev) => [payload, ...prev]);
        setSelectedSurveyId(payload.id);
        setDraft(emptyDraft());
        setOperatorQuery('');
        setShowBuilder(false);
        notify('Опрос создан', 'success');
    };

    const removeSurvey = (id) => {
        const survey = surveys.find((s) => s.id === id);
        if (!survey) return;
        if (!window.confirm(`Удалить опрос "${survey.title}"?`)) return;
        setSurveys((prev) => prev.filter((s) => s.id !== id));
    };

    return (
        <div className="space-y-6">
            <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
                <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                    <div>
                        <h2 className="text-2xl font-semibold text-gray-800">Опросы</h2>
                        <p className="text-sm text-gray-600">Создание опросов с назначением по стажу и направлению.</p>
                    </div>
                    <button onClick={() => setShowBuilder((v) => !v)} className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700">
                        {showBuilder ? 'Закрыть' : 'Создать опрос'}
                    </button>
                </div>
            </div>

            {showBuilder && (
                <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <input value={draft.title} onChange={(e) => setDraft((p) => ({ ...p, title: e.target.value }))} placeholder="Название опроса" className="p-3 border border-gray-300 rounded-lg" />
                        <input value={draft.description} onChange={(e) => setDraft((p) => ({ ...p, description: e.target.value }))} placeholder="Описание (необязательно)" className="p-3 border border-gray-300 rounded-lg" />
                    </div>

                    <div className="space-y-2">
                        <div className="text-sm font-medium text-gray-700">Направления</div>
                        <div className="flex flex-wrap gap-2">{directionOptions.map((d) => <button key={d.id} onClick={() => toggleArrayValue(setDraft, 'directionIds', d.id)} className={`px-3 py-1 rounded-full border text-sm ${draft.directionIds.includes(d.id) ? 'bg-blue-600 text-white border-blue-600' : 'border-gray-300'}`}>{d.name}</button>)}</div>
                    </div>

                    <div className="space-y-2">
                        <div className="text-sm font-medium text-gray-700">Стаж</div>
                        <div className="flex flex-wrap gap-2">{TENURE_BUCKETS.map((t) => <button key={t.id} onClick={() => toggleArrayValue(setDraft, 'tenureBuckets', t.id)} className={`px-3 py-1 rounded-full border text-sm ${draft.tenureBuckets.includes(t.id) ? 'bg-emerald-600 text-white border-emerald-600' : 'border-gray-300'}`}>{t.label}</button>)}</div>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center justify-between"><div className="text-sm font-medium text-gray-700">Операторы ({filteredOperators.length})</div><div className="flex gap-2"><button onClick={() => setDraft((p) => ({ ...p, operatorIds: filteredOperators.map((o) => o.id) }))} className="text-xs px-2 py-1 bg-blue-100 rounded">Выбрать всех</button><button onClick={() => setDraft((p) => ({ ...p, operatorIds: [] }))} className="text-xs px-2 py-1 bg-gray-100 rounded">Очистить</button></div></div>
                        <input value={operatorQuery} onChange={(e) => setOperatorQuery(e.target.value)} placeholder="Поиск оператора" className="w-full p-2.5 border border-gray-300 rounded-lg" />
                        <div className="max-h-52 overflow-y-auto border border-gray-200 rounded-lg divide-y divide-gray-100">{filteredOperators.map((op) => <label key={op.id} className="flex items-center gap-2 p-2 text-sm hover:bg-gray-50"><input type="checkbox" checked={draft.operatorIds.includes(op.id)} onChange={() => toggleArrayValue(setDraft, 'operatorIds', op.id)} /><span className="font-medium">{op.name}</span><span className="text-gray-500">| {op.directionName} | {op.tenureLabel}</span></label>)}</div>
                    </div>

                    <div className="space-y-3">
                        <div className="flex items-center justify-between"><div className="text-sm font-medium text-gray-700">Вопросы</div><button onClick={() => setDraft((p) => ({ ...p, questions: [...p.questions, emptyQuestion()] }))} className="text-xs px-2 py-1 bg-indigo-100 rounded">+ Вопрос</button></div>
                        {draft.questions.map((q, idx) => (
                            <div key={q.id} className="border border-gray-200 rounded-lg p-3 space-y-2">
                                <div className="flex items-center justify-between"><span className="text-xs font-medium text-gray-500">Вопрос #{idx + 1}</span><button disabled={draft.questions.length <= 1} onClick={() => setDraft((p) => ({ ...p, questions: p.questions.filter((x) => x.id !== q.id) }))} className="text-xs px-2 py-1 bg-red-50 text-red-600 rounded">Удалить</button></div>
                                <input value={q.text} onChange={(e) => updateQuestion(q.id, { text: e.target.value })} placeholder="Текст вопроса" className="w-full p-2.5 border border-gray-300 rounded-lg" />
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                    <select value={q.type} onChange={(e) => updateQuestion(q.id, { type: e.target.value, allowOther: e.target.value === 'rating' ? false : q.allowOther, options: e.target.value === 'rating' ? [] : (q.options?.length ? q.options : ['', '']) })} className="p-2.5 border border-gray-300 rounded-lg">{QUESTION_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select>
                                    <div className="flex items-center gap-4 text-sm">
                                        <label className="inline-flex items-center gap-1"><input type="checkbox" checked={!!q.required} onChange={(e) => updateQuestion(q.id, { required: e.target.checked })} />Обязательный</label>
                                        {q.type !== 'rating' && <label className="inline-flex items-center gap-1"><input type="checkbox" checked={!!q.allowOther} onChange={(e) => updateQuestion(q.id, { allowOther: e.target.checked })} />Другой</label>}
                                    </div>
                                </div>
                                {q.type !== 'rating' && <div className="space-y-2">{(q.options || []).map((o, i) => <div key={`${q.id}_${i}`} className="flex gap-2"><input value={o} onChange={(e) => updateQuestion(q.id, { options: (q.options || []).map((v, idx2) => (idx2 === i ? e.target.value : v)) })} placeholder={`Вариант ${i + 1}`} className="flex-1 p-2 border border-gray-300 rounded-lg" /><button disabled={(q.options || []).length <= 2} onClick={() => updateQuestion(q.id, { options: (q.options || []).filter((_, idx2) => idx2 !== i) })} className="px-2 rounded bg-gray-100">-</button></div>)}<button onClick={() => updateQuestion(q.id, { options: [...(q.options || []), ''] })} className="text-xs px-2 py-1 bg-gray-100 rounded">+ Вариант</button></div>}
                            </div>
                        ))}
                    </div>

                    <div className="flex gap-2"><button onClick={createSurvey} className="px-4 py-2 rounded-lg bg-green-600 text-white">Сохранить</button><button onClick={() => { setShowBuilder(false); setDraft(emptyDraft()); setOperatorQuery(''); }} className="px-4 py-2 rounded-lg bg-gray-100">Отмена</button></div>
                </div>
            )}

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
                    <div className="px-4 py-3 border-b border-gray-100 font-semibold text-gray-800">Список опросов</div>
                    <div className="p-4 space-y-2 max-h-[560px] overflow-y-auto">
                        {surveys.length === 0 && <div className="text-sm text-gray-500">Опросов пока нет.</div>}
                        {surveys.map((s) => <div key={s.id} className={`border rounded-lg p-3 ${s.id === selectedSurveyId ? 'border-blue-400 bg-blue-50' : 'border-gray-200'}`}><div className="flex items-start justify-between gap-2"><button onClick={() => setSelectedSurveyId(s.id)} className="text-left flex-1"><div className="font-semibold text-gray-800">{s.title}</div><div className="text-xs text-gray-500 mt-1">Вопросов: {s.questions?.length || 0} | Назначено: {s.assignment?.operatorIds?.length || 0}</div></button><button onClick={() => removeSurvey(s.id)} className="text-xs px-2 py-1 rounded bg-red-50 text-red-600">Удалить</button></div></div>)}
                    </div>
                </div>

                <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
                    <div className="px-4 py-3 border-b border-gray-100 font-semibold text-gray-800">Детали опроса</div>
                    <div className="p-4 space-y-3 max-h-[560px] overflow-y-auto">
                        {!selectedSurvey && <div className="text-sm text-gray-500">Выберите опрос слева.</div>}
                        {selectedSurvey && <>
                            <div><div className="text-xl font-semibold text-gray-900">{selectedSurvey.title}</div>{selectedSurvey.description && <div className="text-sm text-gray-600">{selectedSurvey.description}</div>}</div>
                            <div className="text-xs text-gray-600 rounded-lg border border-gray-200 bg-gray-50 p-3">Направления: {selectedSurvey.assignment?.directionIds?.length ? selectedSurvey.assignment.directionIds.map((id) => directionNameById.get(String(id)) || id).join(', ') : 'Все'}<br />Стаж: {selectedSurvey.assignment?.tenureBuckets?.length ? selectedSurvey.assignment.tenureBuckets.map((id) => TENURE_BUCKETS.find((x) => x.id === id)?.label || id).join(', ') : 'Любой'}<br />Операторов: {selectedSurvey.assignment?.operatorIds?.length || 0}</div>
                            <div className="space-y-2">{(selectedSurvey.questions || []).map((q, i) => <div key={q.id || i} className="border border-gray-200 rounded-lg p-3"><div className="text-xs text-gray-500 mb-1">#{i + 1} | {QUESTION_TYPES.find((t) => t.value === q.type)?.label || q.type}{q.required ? ' | обязательный' : ''}{q.allowOther ? ' | есть "Другой"' : ''}</div><div className="font-medium text-gray-800 mb-1">{q.text}</div>{q.type === 'rating' ? <div className="text-amber-500">★ ★ ★ ★ ★</div> : <ul className="list-disc pl-5 text-sm text-gray-600">{(q.options || []).map((o, oi) => <li key={`${q.id}_${oi}`}>{o}</li>)}{q.allowOther && <li>Другой...</li>}</ul>}</div>)}</div>
                            <div className="space-y-1"><div className="text-sm font-medium text-gray-700">Операторы назначения</div><div className="max-h-36 overflow-y-auto border border-gray-200 rounded-lg divide-y divide-gray-100">{selectedSurveyOperators.length === 0 && <div className="p-2 text-sm text-gray-500">Нет данных</div>}{selectedSurveyOperators.map((op) => <div key={`${selectedSurvey.id}_${op.id}`} className="p-2 text-sm"><div className="font-medium">{op.name}</div><div className="text-xs text-gray-500">{op.directionName} | {op.tenureLabel}</div></div>)}</div></div>
                        </>}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default SurveysView;
