import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createRoot } from 'react-dom/client';
import { createPortal } from 'react-dom';
import './styles.css';
const API_BASE_URL = 'https://otp-2-fos4.onrender.com';
const AUTH_REFRESH_URL = `${API_BASE_URL}/api/auth/refresh`;
const EMBED_STATE_KEY = 'call_evaluation_embed_state';
let refreshPromise = null;
const audioUrlCache = {};

const readEmbedState = () => {
    try {
        const raw = sessionStorage.getItem(EMBED_STATE_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return null;
        return {
            user: parsed.user || null,
            initialSelection: parsed.initialSelection || null
        };
    } catch {
        return null;
    }
};

const writeEmbedState = ({ user = null, initialSelection = null } = {}) => {
    try {
        sessionStorage.setItem(EMBED_STATE_KEY, JSON.stringify({ user, initialSelection }));
    } catch {}
};

const readJsonSafe = async (r) => { try { return await r.json(); } catch { return null; } };

const authFetch = async (url, opts = {}, retry = true) => {
    const res = await fetch(url, { credentials: 'include', ...opts, headers: { ...(opts.headers || {}) } });
    if (res.status !== 401 || !retry) return res;
    const body = await readJsonSafe(res.clone());
    if (!body || body.code !== 'TOKEN_EXPIRED') return res;
    if (!refreshPromise) {
        refreshPromise = fetch(AUTH_REFRESH_URL, { method: 'POST', credentials: 'include' }).finally(() => { refreshPromise = null; });
    }
    const rr = await refreshPromise;
    if (!rr.ok) return res;
    return authFetch(url, opts, false);
};

const getAudioUrl = async (evalId, userId) => {
    if (audioUrlCache[evalId]) return audioUrlCache[evalId];
    try {
        const r = await authFetch(`${API_BASE_URL}/api/audio/${evalId}`, { headers: { 'X-User-Id': userId } });
        if (!r.ok) return null;
        const d = await r.json();
        if (d?.url) { audioUrlCache[evalId] = d.url; return d.url; }
        return null;
    } catch { return null; }
};

const escapeHtml = (s) => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;');

const parseToHtml = (text) => {
    if (!text) return '';
    const lines = text.split(/\r?\n/);
    let html = '', stack = [];
    const closeLists = (lvl = 0) => { while (stack.length > lvl) { html += stack.pop() === 'ol' ? '</ol>' : '</ul>'; } };
    for (const raw of lines) {
        const line = raw.replace(/\t/g, '    ');
        if (!line.trim()) { closeLists(); html += '<p>&nbsp;</p>'; continue; }
        const om = line.match(/^\s*(\d+(?:\.\d+)*)\.\s*(.*)$/);
        if (om) {
            const lvl = om[1].split('.').length;
            if (stack.length < lvl) { for (let j = stack.length; j < lvl; j++) { html += `<ol style="padding-left:18px;margin-bottom:6px">`; stack.push('ol'); } }
            else if (stack.length > lvl) closeLists(lvl);
            html += `<li>${escapeHtml(om[2].trim())}</li>`; continue;
        }
        const ulm = line.match(/^\s*[-•*]\s+(.*)/);
        if (ulm) {
            if (!stack.length || stack[stack.length-1] !== 'ul') { html += '<ul style="padding-left:18px;margin-bottom:6px">'; stack.push('ul'); }
            html += `<li>${escapeHtml(ulm[1].trim())}</li>`; continue;
        }
        closeLists();
        html += `<p style="margin-bottom:6px">${escapeHtml(line)}</p>`;
    }
    closeLists();
    return html;
};

// ─── Score Toggle Button ───────────────────────────────
const ScoreToggle = ({ label, value, active, onClick }) => (
    <button
        type="button"
        onClick={onClick}
        className={`score-toggle ${active ? `active-${value.toLowerCase()}` : ''}`}
    >{label}</button>
);

// ─── Criterion Card ────────────────────────────────────
const CriterionCard = ({ criterion, index, score, comment, commentVisible, onScoreChange, onCommentChange, onToggleComment, onShowInfo }) => {
    const isNeg = score === 'Error' || score === 'Incorrect' || score === 'Deficiency';
    return (
        <div className={`crit-card ${isNeg ? 'is-error' : 'is-correct'}`}>
            <div className="crit-card-header">
                <div className="crit-card-name" style={criterion.isCritical ? {color:'var(--red)'} : {}}>
                    {criterion.name}
                </div>
                <span className={`crit-weight ${criterion.isCritical ? 'critical' : ''}`}>
                    {criterion.isCritical ? 'Критерий' : `${criterion.weight} pts`}
                </span>
                <button className={`crit-comment-toggle ${commentVisible ? 'active' : ''}`} onClick={onToggleComment} title="Комментарий">
                    <i className="fa-regular fa-comment-dots" />
                </button>
                <button className="crit-info-btn" onClick={onShowInfo} title="Описание критерия">
                    <i className="fa-regular fa-circle-question" />
                </button>
            </div>
            <div className="crit-card-body">
                <div className="score-toggles">
                    <ScoreToggle label="Корректно" value="Correct" active={score === 'Correct'} onClick={() => onScoreChange('Correct')} />
                    <ScoreToggle label="N/A" value="na" active={score === 'N/A'} onClick={() => onScoreChange('N/A')} />
                    {!criterion.isCritical && (
                        <>
                            <ScoreToggle label="Ошибка" value="Incorrect" active={score === 'Incorrect'} onClick={() => onScoreChange('Incorrect')} />
                            {criterion.deficiency && (
                                <ScoreToggle label="Недочёт" value="Deficiency" active={score === 'Deficiency'} onClick={() => onScoreChange('Deficiency')} />
                            )}
                        </>
                    )}
                    {criterion.isCritical && (
                        <ScoreToggle label="Критич. ошибка" value="Error" active={score === 'Error'} onClick={() => onScoreChange('Error')} />
                    )}
                </div>

                {(isNeg || commentVisible) && (
                    <div className="comment-area" style={{marginTop: 8}}>
                        <textarea
                            className="textarea"
                            style={{marginTop: 0, minHeight: 64}}
                            value={comment || ''}
                            onChange={e => onCommentChange(e.target.value)}
                            placeholder={isNeg ? `Укажите причину ошибки в критерии "${criterion.name}"` : `Комментарий (необязательно)`}
                            rows={2}
                        />
                        {isNeg && !comment?.trim() && (
                            <div className="error-text">Комментарий обязателен</div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};


// ─── SV Request Button ─────────────────────────────────
const HoverTooltip = ({ text, children }) => {
    const triggerRef = useRef(null);
    const [isOpen, setIsOpen] = useState(false);
    const [placement, setPlacement] = useState('top');
    const [position, setPosition] = useState({ left: 0, top: 0 });

    const recalcPosition = useCallback(() => {
        if (!triggerRef.current) return;
        const rect = triggerRef.current.getBoundingClientRect();
        const halfTooltipWidth = 120;
        const left = Math.min(
            window.innerWidth - halfTooltipWidth - 12,
            Math.max(halfTooltipWidth + 12, rect.left + rect.width / 2)
        );
        const shouldShowTop = rect.top > 110;
        setPlacement(shouldShowTop ? 'top' : 'bottom');
        setPosition({
            left,
            top: shouldShowTop ? rect.top - 8 : rect.bottom + 8
        });
    }, []);

    useEffect(() => {
        if (!isOpen) return;
        recalcPosition();
        const handleViewportChange = () => recalcPosition();
        window.addEventListener('scroll', handleViewportChange, true);
        window.addEventListener('resize', handleViewportChange);
        return () => {
            window.removeEventListener('scroll', handleViewportChange, true);
            window.removeEventListener('resize', handleViewportChange);
        };
    }, [isOpen, recalcPosition]);

    return (
        <span
            ref={triggerRef}
            className="tooltip-wrap"
            onMouseEnter={() => { setIsOpen(true); recalcPosition(); }}
            onMouseLeave={() => setIsOpen(false)}
            onFocus={() => { setIsOpen(true); recalcPosition(); }}
            onBlur={() => setIsOpen(false)}
        >
            {children}
            {isOpen && createPortal(
                <div
                    className={`tooltip-box ${placement === 'bottom' ? 'bottom' : ''}`}
                    role="tooltip"
                    style={{ left: position.left, top: position.top }}
                >
                    {text}
                </div>,
                document.body
            )}
        </span>
    );
};

const SvRequestButton = ({ call, userId, userRole, fetchEvaluations, onReevaluate }) => {
    const [showModal, setShowModal] = useState(false);
    const [comment, setComment] = useState('');
    const [loading, setLoading] = useState(false);
    const isSv = String(userRole) === 'sv';
    if (!isSv) return null;

    const submit = async () => {
        setLoading(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation/sv_request`, {
                method: 'POST', headers: {'Content-Type':'application/json', 'X-User-Id': userId},
                body: JSON.stringify({ call_id: call.id, comment })
            });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error);
            await fetchEvaluations?.({ force: true });
            setShowModal(false); setComment('');
            alert('Заявка отправлена');
        } catch(e) { alert('Ошибка: ' + e.message); }
        finally { setLoading(false); }
    };

    if (!call.sv_request) return (
        <>
            <button className="btn btn-amber btn-sm" onClick={e => { e.stopPropagation(); setShowModal(true); }}>Запрос</button>
            {showModal && (
                <div className="modal-backdrop" onClick={e => { e.stopPropagation(); setShowModal(false); }}>
                    <div className="modal request-modal" onClick={e => e.stopPropagation()}>
                        <div className="modal-header">
                            <div><h2>Запрос на переоценку</h2><div className="modal-header-sub">Call ID: {call.id}</div></div>
                            <button className="close-btn" onClick={() => setShowModal(false)}><i className="fas fa-times"/></button>
                        </div>
                        <div className="modal-body">
                            <div className="field">
                                <label className="label">Комментарий (необязательно)</label>
                                <textarea className="textarea" value={comment} onChange={e => setComment(e.target.value)} placeholder="Опишите причину запроса..." />
                            </div>
                        </div>
                        <div className="modal-footer">
                            <button className="btn btn-secondary" onClick={() => setShowModal(false)}>Отмена</button>
                            <button className="btn btn-amber" onClick={submit} disabled={loading}>{loading ? <><span className="spinner" style={{borderTopColor:'var(--amber)'}} /> Отправка...</> : 'Отправить'}</button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );

    if (call.sv_request && !call.sv_request_approved) return (
        <HoverTooltip text={[call.sv_request_comment && `Комментарий: ${call.sv_request_comment}`, call.sv_request_by_name && `От: ${call.sv_request_by_name}`].filter(Boolean).join('\n') || 'Запрос на рассмотрении'}>
            <span style={{fontSize:13, color:'var(--amber)', display:'flex', alignItems:'center', gap:4}}>
                <i className="fas fa-clock" style={{fontSize:11}} /> Ожидает
            </span>
        </HoverTooltip>
    );

    if (call.sv_request_approved) return (
        <div style={{display:'flex', alignItems:'center', gap:6}}>
            <HoverTooltip text={[call.sv_request_comment && `Комм.: ${call.sv_request_comment}`, call.sv_request_by_name && `Запросил: ${call.sv_request_by_name}`, call.sv_request_approved_by_name && `Одобрил: ${call.sv_request_approved_by_name}`].filter(Boolean).join('\n') || 'Запрос одобрен'}>
                <i className="fas fa-info-circle" style={{color:'var(--green)', cursor:'pointer'}} />
            </HoverTooltip>
            <button className="btn btn-primary btn-sm" onClick={e => { e.stopPropagation(); onReevaluate(); }}>Переоценить</button>
        </div>
    );
    return null;
};

// ─── Date Range Picker ─────────────────────────────────
const DateRangePicker = ({ minDate, maxDate, setFromDate, setToDate }) => {
    const ref = useRef(null);
    const [label, setLabel] = useState('');

    useEffect(() => {
        if (!ref.current) return;
        flatpickr(ref.current, {
            mode: 'range', dateFormat: 'Y-m-d', minDate, maxDate,
            onChange(dates) {
                if (dates.length === 2) {
                    const end = new Date(dates[1]);
                    end.setDate(end.getDate() + 1); end.setMilliseconds(-1);
                    setFromDate(dates[0].toISOString());
                    setToDate(end.toISOString());
                    setLabel(`${dates[0].toISOString().slice(0,10)} — ${dates[1].toISOString().slice(0,10)}`);
                } else { setFromDate(null); setToDate(null); setLabel(''); }
            }
        });
    }, [minDate, maxDate]);

    return (
        <div className="filter-group">
            <label className="label">Период</label>
            <input ref={ref} className="input" type="text" placeholder="Выбрать период" readOnly style={{minWidth:200, cursor:'pointer'}} />
        </div>
    );
};

// ─── Evaluation Modal ──────────────────────────────────
const EvaluationModal = ({ isOpen, onClose, onSubmit, directions, operator, selectedMonth, userId, userName, existingEvaluation }) => {
    const [callFile, setCallFile] = useState(null);
    const [audioUrl, setAudioUrl] = useState(null);
    const [audioError, setAudioError] = useState(null);
    const [scores, setScores] = useState([]);
    const [comments, setComments] = useState([]);
    const [commentVisible, setCommentVisible] = useState([]);
    const [phoneNumber, setPhoneNumber] = useState('');
    const [phoneError, setPhoneError] = useState('');
    const [appealDate, setAppealDate] = useState('');
    const [assignedMonth, setAssignedMonth] = useState(selectedMonth);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [selectedDirId, setSelectedDirId] = useState(null);
    const [infoIndex, setInfoIndex] = useState(null);
    const [expectedDuration, setExpectedDuration] = useState(null);
    const [actualDuration, setActualDuration] = useState(null);
    const [durationMismatch, setDurationMismatch] = useState(false);
    const isLocked = !!(existingEvaluation?.isReevaluation || existingEvaluation?.is_imported);

    const currentDir = directions?.find(d => d.id === selectedDirId) || directions?.[0] || null;
    const criteria = currentDir?.criteria || [];
    const monthsRu = ['янв.','февр.','мар.','апр.','май','июн.','июл.','авг.','сент.','окт.','ноя.','дек.'];

    const MIN_TOL = 3, PCT_TOL = 0.15;
    const fmtSec = (s) => {
        if (!s) return '—';
        const t = Math.round(s), h = Math.floor(t/3600), m = Math.floor((t%3600)/60), sec = t%60;
        return h > 0 ? `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}` : `${m}:${String(sec).padStart(2,'0')}`;
    };

    useEffect(() => {
        if (!isOpen || !directions?.length) return;
        const initId = existingEvaluation
            ? (existingEvaluation.directionId ?? existingEvaluation._rawEvaluation?.direction_id ?? directions.find(d=>d.name===existingEvaluation.selectedDirection)?.id ?? operator?.direction_id ?? directions[0]?.id)
            : (operator?.direction_id ?? directions[0]?.id);
        setSelectedDirId(initId);
        const initDir = directions.find(d=>d.id===initId) || directions[0];

        if (existingEvaluation) {
            if (existingEvaluation.is_imported) {
                const ed = existingEvaluation.duration || existingEvaluation._rawEvaluation?.duration || null;
                setExpectedDuration(ed ? parseFloat(ed) : null);
                setActualDuration(null); setDurationMismatch(false);
                setPhoneNumber(existingEvaluation.phoneNumber || '');
                const date = new Date(existingEvaluation.appeal_date);
                setAppealDate(initDir?.hasFileUpload
                    ? `${date.getDate().toString().padStart(2,'0')}-${(date.getMonth()+1).toString().padStart(2,'0')}-${date.getFullYear()} ${date.getHours().toString().padStart(2,'0')}:${date.getMinutes().toString().padStart(2,'0')}:${date.getSeconds().toString().padStart(2,'0')}`
                    : `${date.getDate()} ${monthsRu[date.getMonth()]} ${date.getHours().toString().padStart(2,'0')}:${date.getMinutes().toString().padStart(2,'0')}`);
                setScores(initDir?.criteria?.map(()=>'Correct') || []);
                setComments(initDir?.criteria?.map(()=>'') || []);
                setCommentVisible(initDir?.criteria?.map(()=>false) || []);
                setAssignedMonth(selectedMonth); setAudioUrl(null); setCallFile(null); setPhoneError('');
            } else {
                setScores(existingEvaluation.scores || []);
                setComments(existingEvaluation.criterionComments || []);
                setCommentVisible((existingEvaluation.criterionComments||[]).map(c=>!!(c&&c.trim())));
                setPhoneNumber(existingEvaluation.phoneNumber || '');
                setAssignedMonth(existingEvaluation.assignedMonth || selectedMonth);
                setAudioUrl(existingEvaluation.audioUrl || null);
                setActualDuration(null); setDurationMismatch(false);
                if (existingEvaluation.appeal_date) {
                    const date = new Date(existingEvaluation.appeal_date);
                    setAppealDate(initDir?.hasFileUpload
                        ? `${date.getDate().toString().padStart(2,'0')}-${(date.getMonth()+1).toString().padStart(2,'0')}-${date.getFullYear()} ${date.getHours().toString().padStart(2,'0')}:${date.getMinutes().toString().padStart(2,'0')}:${date.getSeconds().toString().padStart(2,'0')}`
                        : `${date.getDate()} ${monthsRu[date.getMonth()]} ${date.getHours().toString().padStart(2,'0')}:${date.getMinutes().toString().padStart(2,'0')}`);
                } else setAppealDate('');
                setPhoneError('');
            }
        } else {
            setExpectedDuration(null); setActualDuration(null); setDurationMismatch(false);
            setScores(initDir?.criteria?.map(()=>'Correct') || []);
            setComments(initDir?.criteria?.map(()=>'') || []);
            setCommentVisible(initDir?.criteria?.map(()=>false) || []);
            setCallFile(null); setAudioUrl(null); setPhoneNumber(''); setAppealDate(''); setPhoneError('');
            setAssignedMonth(selectedMonth);
        }
    }, [isOpen, existingEvaluation, directions, selectedMonth, operator?.direction_id]);

    useEffect(() => {
        if (!existingEvaluation?.id || !currentDir?.hasFileUpload || !userId || existingEvaluation?.is_imported) return;
        getAudioUrl(existingEvaluation.id, userId).then(url => { if (url) setAudioUrl(url); else setAudioError('Не удалось загрузить аудио'); });
    }, [existingEvaluation, userId, currentDir?.hasFileUpload]);

    const handleFile = (e) => {
        const file = e.target.files[0];
        setCallFile(file);
        const url = file ? URL.createObjectURL(file) : null;
        setAudioUrl(url); setAudioError(null); setActualDuration(null); setDurationMismatch(false);
        if (file && url) {
            const audio = new Audio(url);
            audio.addEventListener('loadedmetadata', () => {
                const dur = audio.duration;
                setActualDuration(dur);
                if (expectedDuration) {
                    const allowed = Math.max(MIN_TOL, expectedDuration * PCT_TOL);
                    if (Math.abs(dur - expectedDuration) > allowed) { setDurationMismatch(true); setAudioError(`Длительность файла (${fmtSec(dur)}) ≠ ожидаемой (${fmtSec(expectedDuration)})`); }
                    else setDurationMismatch(false);
                }
            });
            audio.addEventListener('error', () => setAudioError('Не удалось прочитать аудио файл'));
            audio.load();
        }
    };

    const fmtDate = (input) => {
        const hf = currentDir?.hasFileUpload;
        if (hf) {
            let d = input.replace(/\D/g,'').slice(0,14);
            let f = '';
            if (d.length > 0) f += d.slice(0,2);
            if (d.length > 2) f += '-' + d.slice(2,4);
            if (d.length > 4) f += '-' + d.slice(4,8);
            if (d.length > 8) f += ' ' + d.slice(8,10);
            if (d.length > 10) f += ':' + d.slice(10,12);
            if (d.length > 12) f += ':' + d.slice(12,14);
            return f;
        } else {
            const hasMonth = monthsRu.some(m => input.toLowerCase().includes(m.toLowerCase()));
            if (hasMonth) return input;
            let d = input.replace(/\D/g,'');
            if (d.length <= 2) return d;
            if (d.length <= 4) { const m = parseInt(d.slice(2,4)); return m>=1&&m<=12 ? d.slice(0,2)+' '+monthsRu[m-1] : d.slice(0,2); }
            if (d.length <= 6) { const m = parseInt(d.slice(2,4)); return m>=1&&m<=12 ? d.slice(0,2)+' '+monthsRu[m-1]+' '+d.slice(4,6) : d.slice(0,2)+' '+d.slice(4,6); }
            const m = parseInt(d.slice(2,4));
            return m>=1&&m<=12 ? d.slice(0,2)+' '+monthsRu[m-1]+' '+d.slice(4,6)+':'+d.slice(6,8) : d.slice(0,2)+' '+d.slice(4,6)+':'+d.slice(6,8);
        }
    };

    const getAppealDateISO = () => {
        if (!appealDate) return null;
        const hf = currentDir?.hasFileUpload;
        if (hf) {
            let d = appealDate.replace(/\D/g,'');
            const isRe = existingEvaluation?.id != null;
            if (isRe) { if (d.length < 12) return null; const s = d.length>=14 ? d.slice(12,14) : '00'; return `${d.slice(4,8)}-${d.slice(2,4)}-${d.slice(0,2)}T${d.slice(8,10)}:${d.slice(10,12)}:${s}`; }
            if (d.length !== 14) return null;
            return `${d.slice(4,8)}-${d.slice(2,4)}-${d.slice(0,2)}T${d.slice(8,10)}:${d.slice(10,12)}:${d.slice(12,14)}`;
        } else {
            const m = appealDate.trim().match(/^(\d{1,2})\s+([а-яё]+\.?)\s+(\d{1,2}):(\d{2})$/i);
            if (!m) return null;
            const mi = monthsRu.findIndex(x => x.replace(/\./,'').toLowerCase() === m[2].toLowerCase().replace(/\./,''));
            if (mi === -1) return null;
            return `${new Date().getFullYear()}-${String(mi+1).padStart(2,'0')}-${m[1].padStart(2,'0')}T${m[3].padStart(2,'0')}:${m[4]}:00`;
        }
    };

    const handleDirChange = (id) => {
        setSelectedDirId(id);
        const d = directions.find(x=>x.id===id) || directions[0];
        if (d?.criteria) { setScores(d.criteria.map(()=>'Correct')); setComments(d.criteria.map(()=>'')); setCommentVisible(d.criteria.map(()=>false)); }
    };

    const hasCriticalError = criteria.some((c,i) => c.isCritical && scores[i]==='Error');
    const totalScore = hasCriticalError ? 0 : criteria.reduce((sum, c, i) => {
        if (c.isCritical) return sum;
        if (scores[i]==='Correct'||scores[i]==='N/A') return sum + c.weight;
        if (scores[i]==='Deficiency'&&c.deficiency) return sum + c.deficiency.weight;
        return sum;
    }, 0);

    const isSubmitDisabled = !currentDir || !criteria.length ||
        (currentDir?.hasFileUpload && !callFile && !audioUrl) ||
        scores.some((s,i) => (s==='Error'||s==='Incorrect') && !comments[i]?.trim()) ||
        durationMismatch;

    const handleSubmit = async (draft = false) => {
        setIsSubmitting(true);
        const fd = new FormData();
        fd.append('evaluator', userName);
        fd.append('operator', operator.name);
        fd.append('phone_number', phoneNumber);
        fd.append('score', totalScore);
        fd.append('comment', criteria.map((c,i)=>comments[i]?`${c.name}: ${comments[i]}`:'').filter(Boolean).join('; '));
        fd.append('month', assignedMonth);
        fd.append('is_draft', draft);
        fd.append('scores', JSON.stringify(scores));
        fd.append('criterion_comments', JSON.stringify(comments));
        fd.append('direction', currentDir?.id ?? operator?.direction_id);
        const ad = getAppealDateISO();
        if (ad) fd.append('appeal_date', ad);
        if (existingEvaluation?.isReevaluation) { fd.append('previous_version_id', existingEvaluation.id); fd.append('is_correction', true); }
        if (callFile) fd.append('audio_file', callFile);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation`, { method:'POST', headers:{'X-User-Id':userId}, body: fd });
            const res = await r.json();
            if (res.status === 'success') {
                onSubmit({ id: res.evaluation_id, evaluator: userName, operator: operator.name, phoneNumber, totalScore: totalScore.toFixed(2), comment: fd.get('comment'), selectedDirection: currentDir?.name, directionId: currentDir?.id, is_imported: false, directions: [{name: currentDir?.name, hasFileUpload: currentDir?.hasFileUpload, criteria}], scores, criterionComments: comments, audioUrl: currentDir?.hasFileUpload ? audioUrl : null, isDraft: draft, assignedMonth, isCorrection: existingEvaluation?.isReevaluation || false, appeal_date: ad });
                onClose();
            } else alert('Ошибка: ' + res.error);
        } catch(e) { alert('Ошибка отправки: ' + e.message); }
        finally { setIsSubmitting(false); }
    };

    const handleDeleteDraft = async () => {
        if (!existingEvaluation?.isDraft) return;
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation/${existingEvaluation.id}`, { method:'DELETE', headers:{'X-User-Id':userId} });
            const res = await r.json();
            if (res.status === 'success') { onSubmit(null); onClose(); }
            else alert('Ошибка удаления: ' + res.error);
        } catch(e) { alert('Ошибка: ' + e.message); }
    };

    if (!isOpen) return null;
    const title = existingEvaluation?.isReevaluation ? 'Переоценка' : existingEvaluation?.isDraft ? 'Редактирование черновика' : 'Новая оценка';

    return (
        <div className="modal-backdrop">
            <div className="modal modal-wide" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <div>
                        <h2>{title}</h2>
                        <div className="modal-header-sub">Оператор: {operator?.name}</div>
                    </div>
                    <button className="close-btn" onClick={onClose}><i className="fas fa-times" /></button>
                </div>
                <div className="modal-body">
                    {/* Direction selector */}
                    {directions?.length > 1 && (
                        <div style={{marginBottom: 16}}>
                            <label className="label">Направление</label>
                            <div className="dir-tabs">
                                {directions.map(d => (
                                    <button key={d.id} className={`dir-tab ${selectedDirId === d.id ? 'active' : ''}`} onClick={() => handleDirChange(d.id)}>{d.name}</button>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Audio upload */}
                    {currentDir?.hasFileUpload && (
                        <>
                            <div className="section-divider">Аудиозапись</div>
                            {!existingEvaluation?.isReevaluation && (
                                <div className="file-input-wrap" style={{marginBottom: 10}}>
                                    <label htmlFor="audioFile" className={`file-input-label ${callFile ? 'has-file' : ''}`}>
                                        <i className={`fas ${callFile ? 'fa-check-circle' : 'fa-cloud-upload-alt'}`} />
                                        {callFile ? callFile.name : 'Нажмите для загрузки аудиофайла'}
                                    </label>
                                    <input id="audioFile" type="file" accept="audio/*" onChange={handleFile} />
                                </div>
                            )}
                            {audioUrl && (
                                <div className="audio-wrap">
                                    <div className="audio-label">Прослушать запись</div>
                                    <audio controls style={{width:'100%'}}><source src={audioUrl} type="audio/mpeg" /></audio>
                                </div>
                            )}
                            {(expectedDuration || actualDuration) && (
                                <div className="duration-info">
                                    <span>Ожидаемая: <strong>{fmtSec(expectedDuration)}</strong></span>
                                    <span>Фактическая: <strong>{fmtSec(actualDuration) || '—'}</strong></span>
                                </div>
                            )}
                            {durationMismatch && <div className="duration-error"><i className="fas fa-exclamation-circle" />{audioError}</div>}
                            {audioError && !durationMismatch && <div className="error-text">{audioError}</div>}
                        </>
                    )}

                    {/* Phone + Date */}
                    <div className="section-divider">Данные обращения</div>
                    <div className="grid-2">
                        <div className="field">
                            <label className="label">Номер телефона</label>
                            <input
                                className="input"
                                type="text"
                                value={phoneNumber}
                                onChange={e => { const v = e.target.value.replace(/[^0-9+]/g,''); setPhoneNumber(v); setPhoneError(v.length < 5 ? 'Слишком короткий номер' : ''); }}
                                placeholder="+7 000 000 0000"
                                readOnly={isLocked}
                            />
                            {phoneError && <div className="error-text">{phoneError}</div>}
                        </div>
                        <div className="field">
                            <label className="label">Дата обращения</label>
                            <input
                                className="input"
                                type="text"
                                value={appealDate}
                                onChange={e => setAppealDate(fmtDate(e.target.value))}
                                placeholder={currentDir?.hasFileUpload ? 'DD-MM-YYYY HH:MM:SS' : 'DD месяц HH:MM'}
                                readOnly={isLocked}
                                style={{fontFamily: 'var(--font-mono)'}}
                            />
                        </div>
                    </div>

                    {/* Criteria */}
                    <div className="section-divider">Критерии оценивания</div>
                    {!criteria.length ? (
                        <div style={{padding:'12px',color:'var(--red)',fontSize:13}}>У направления нет критериев.</div>
                    ) : (
                        <div style={{maxHeight: 380, overflowY: 'auto', paddingRight: 4}}>
                            {criteria.map((criterion, i) => (
                                <CriterionCard
                                    key={i}
                                    criterion={criterion}
                                    index={i}
                                    score={scores[i] || 'Correct'}
                                    comment={comments[i]}
                                    commentVisible={commentVisible[i]}
                                    onScoreChange={val => { const s=[...scores]; s[i]=val; setScores(s); }}
                                    onCommentChange={val => { const c=[...comments]; c[i]=val; setComments(c); }}
                                    onToggleComment={() => { const v=[...commentVisible]; v[i]=!v[i]; setCommentVisible(v); }}
                                    onShowInfo={() => setInfoIndex(infoIndex===i ? null : i)}
                                />
                            ))}
                        </div>
                    )}

                    {/* Score summary */}
                    <div className="score-summary" style={{marginTop:12, borderRadius:'var(--radius)', border:'1px solid var(--border)'}}>
                        <span style={{fontSize:13, color:'var(--text-2)'}}>Итоговый балл</span>
                        <span className="score-summary-val" style={{color: hasCriticalError ? 'var(--red)' : totalScore >= 70 ? 'var(--green)' : totalScore >= 50 ? 'var(--amber)' : 'var(--red)'}}>
                            {hasCriticalError ? '0' : totalScore} / 100
                        </span>
                    </div>
                </div>

                <div className="modal-footer">
                    {existingEvaluation?.isDraft && (
                        <button className="btn btn-danger" onClick={handleDeleteDraft} style={{marginRight:'auto'}}>
                            <i className="fas fa-trash" /> Удалить черновик
                        </button>
                    )}
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary" onClick={() => handleSubmit(false)} disabled={isSubmitDisabled || isSubmitting}>
                        {isSubmitting ? <><span className="spinner" /> Отправка...</> : <><i className="fas fa-check" /> Отправить</>}
                    </button>
                </div>
            </div>

            {/* Info side panel */}
            {infoIndex !== null && criteria[infoIndex] && (
                <div className="info-panel" onClick={e => e.stopPropagation()}>
                    <div className="info-panel-header">
                        <span className="info-panel-title">{criteria[infoIndex].name}</span>
                        <button className="close-btn" onClick={() => setInfoIndex(null)}><i className="fas fa-times" /></button>
                    </div>
                    <div className="info-panel-body" dangerouslySetInnerHTML={{__html: parseToHtml(String(criteria[infoIndex].value || 'Описание отсутствует'))}} />
                </div>
            )}
        </div>
    );
};

// ─── Main App ──────────────────────────────────────────
const App = ({ user, initialSelection }) => {
    const userId = user?.id;
    const userRole = user?.role;
    const userName = user?.name;
    const [calls, setCalls] = useState([]);
    const [directions, setDirections] = useState([]);
    const [operators, setOperators] = useState([]);
    const [supervisors, setSupervisors] = useState([]);
    const [selectedOperator, setSelectedOperator] = useState(null);
    const [selectedSupervisor, setSelectedSupervisor] = useState(null);
    const [selectedMonth, setSelectedMonth] = useState(new Date().toISOString().slice(0,7));
    const [expandedId, setExpandedId] = useState(null);
    const [editingEval, setEditingEval] = useState(null);
    const [showEvalModal, setShowEvalModal] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [loadingCallId, setLoadingCallId] = useState(null);
    const [operatorFromToken, setOperatorFromToken] = useState(null);
    const [fromDate, setFromDate] = useState(null);
    const [toDate, setToDate] = useState(null);
    const [viewMode, setViewMode] = useState('normal');
    const [showVersionsModal, setShowVersionsModal] = useState(false);
    const [versionHistory, setVersionHistory] = useState([]);
    const operatorsCacheRef = useRef(new Map());
    const callsCacheRef = useRef(new Map());
    const MAX_EVALS = 20;
    const normalizeStatus = (status) => String(status ?? '').trim().toLowerCase();
    const isFiredStatus = (status) => {
        const s = normalizeStatus(status);
        return s === 'fired' || s === 'dismissed' || s === 'terminated' || s === 'уволен';
    };
    const compareByNameRu = (a, b) => String(a?.name ?? '').localeCompare(String(b?.name ?? ''), 'ru');

    const fmtDate = (ds) => {
        if (!ds) return '—';
        try {
            const d = new Date(String(ds).replace(' ','T'));
            if (isNaN(d)) return ds;
            return new Intl.DateTimeFormat('ru-RU', {day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(d).replace(/\./g,'').replace(',','');
        } catch { return ds; }
    };

    const months = Array.from({length:12},(_,i) => {
        const d = new Date(new Date().getFullYear(), new Date().getMonth()-i, 1);
        return { value: `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`, label: d.toLocaleString('ru',{month:'long',year:'numeric'}) };
    });

    const getOperatorsCacheKey = useCallback((scopeId) => `${userRole || 'unknown'}:${scopeId || 'none'}`, [userRole]);
    const getCallsCacheKey = useCallback((operatorId, month) => `${operatorId}:${month}`, []);
    const mapEvaluationToCall = useCallback((ev, operator) => ({
        id: ev.id,
        fileName: `Call ${ev.phone_number}`,
        totalScore: ev.score != null ? parseFloat(ev.score).toFixed(2) : null,
        date: ev.evaluation_date ? ev.evaluation_date.split('T')[0] : '',
        phoneNumber: ev.phone_number,
        combinedComment: ev.comment,
        appeal_date: ev.appeal_date || '-',
        selectedDirection: ev.direction?.name || operator?.direction || '-',
        directionId: ev.direction?.id ?? null,
        directions: [{name: ev.direction?.name || '-', hasFileUpload: ev.direction?.hasFileUpload ?? true, criteria: ev.direction?.criteria || []}],
        scores: ev.scores || [],
        criterionComments: ev.criterion_comments || [],
        audioUrl: null,
        isDraft: ev.is_draft,
        assignedMonth: ev.month,
        isCorrection: ev.is_correction || false,
        is_imported: ev.is_imported || false,
        sv_request: !!ev.sv_request,
        sv_request_comment: ev.sv_request_comment || null,
        sv_request_by: ev.sv_request_by || null,
        sv_request_by_name: ev.sv_request_by_name || null,
        sv_request_at: ev.sv_request_at || null,
        sv_request_approved: !!ev.sv_request_approved,
        sv_request_approved_by: ev.sv_request_approved_by || null,
        sv_request_approved_by_name: ev.sv_request_approved_by_name || null,
        sv_request_approved_at: ev.sv_request_approved_at || null,
        _rawEvaluation: ev
    }), []);

    // Supervisors
    useEffect(() => {
        if (userRole !== 'admin' || !userId) return;
        authFetch(`${API_BASE_URL}/api/admin/sv_list`, { headers:{'X-User-Id':userId} })
            .then(r=>r.json()).then(d=>{ if(d.status==='success') setSupervisors(d.sv_list||[]); }).catch(console.error);
    }, [userRole, userId]);

    useEffect(() => {
        if (!initialSelection) return;
        const id = Number(initialSelection.operatorId);
        if (id) {
            setOperatorFromToken({
                id,
                name: initialSelection.operatorName || ''
            });
        }
        if (initialSelection.month) setSelectedMonth(initialSelection.month);
        if (initialSelection.supervisorId != null) setSelectedSupervisor(Number(initialSelection.supervisorId) || null);
    }, [initialSelection]);

    useEffect(() => {
        if (!userId) return;
        writeEmbedState({
            user: { id: userId, role: userRole, name: userName },
            initialSelection: {
                operatorId: selectedOperator?.id || null,
                operatorName: selectedOperator?.name || '',
                supervisorId: selectedSupervisor || null,
                month: selectedMonth
            }
        });
    }, [userId, userRole, userName, selectedOperator, selectedSupervisor, selectedMonth]);

    useEffect(() => {
        if (operatorFromToken && operators.length > 0) {
            setSelectedOperator(operators.find(op=>op.id===operatorFromToken.id) || null);
            setOperatorFromToken(null);
        }
    }, [operators, operatorFromToken]);

    // Directions
    useEffect(() => {
        if (!userId) return;
        authFetch(`${API_BASE_URL}/api/admin/directions`, {headers:{'X-User-Id':userId}})
            .then(r => r.json())
            .then(d => { if (d.status === 'success') setDirections(d.directions || []); })
            .catch(console.error);
    }, [userId]);

    // Operators
    useEffect(() => {
        if (!userId) return;
        const scopeId = userRole === 'admin' ? selectedSupervisor : userId;
        if (!scopeId) {
            setOperators([]);
            return;
        }

        const cacheKey = getOperatorsCacheKey(scopeId);
        const cachedOperators = operatorsCacheRef.current.get(cacheKey);
        if (cachedOperators) {
            setOperators(cachedOperators);
            return;
        }

        let isCancelled = false;
        setIsLoading(true);
        authFetch(`${API_BASE_URL}/api/sv/data?id=${scopeId}`, {headers:{'X-User-Id':userId}})
            .then(r => r.json())
            .then(d => {
                if (isCancelled) return;
                if (d.status === 'success') {
                    const nextOperators = d.operators || [];
                    operatorsCacheRef.current.set(cacheKey, nextOperators);
                    setOperators(nextOperators);
                } else {
                    setOperators([]);
                }
            })
            .catch((e) => {
                if (!isCancelled) {
                    setOperators([]);
                    console.error(e);
                }
            })
            .finally(() => {
                if (!isCancelled) setIsLoading(false);
            });

        return () => { isCancelled = true; };
    }, [userId, userRole, selectedSupervisor, getOperatorsCacheKey]);

    // Evaluations fetch
    const fetchEvaluations = useCallback(async ({ force = false } = {}) => {
        if (!selectedOperator || !userId) { setCalls([]); return; }
        const isOperatorFromLoadedList = operators.some(op => op.id === selectedOperator.id);
        if (!isOperatorFromLoadedList) { setCalls([]); return; }
        const cacheKey = getCallsCacheKey(selectedOperator.id, selectedMonth);
        if (!force && callsCacheRef.current.has(cacheKey)) {
            setCalls(callsCacheRef.current.get(cacheKey) || []);
            return;
        }
        setIsLoading(true);
        try {
            const params = new URLSearchParams({
                operator_id: String(selectedOperator.id),
                month: selectedMonth
            });
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluations?${params.toString()}`, {headers:{'X-User-Id':userId}});
            const d = await r.json();
            if (d.status === 'success') {
                const nextCalls = (d.evaluations || []).map(ev => mapEvaluationToCall(ev, selectedOperator));
                callsCacheRef.current.set(cacheKey, nextCalls);
                setCalls(nextCalls);
            }
        } catch(e) { console.error(e); }
        finally { setIsLoading(false); }
    }, [selectedOperator, userId, selectedMonth, operators, getCallsCacheKey, mapEvaluationToCall]);

    useEffect(() => { fetchEvaluations(); }, [fetchEvaluations]);
    useEffect(() => { setFromDate(null); setToDate(null); }, [selectedMonth]);

    const handleEvaluateCall = (data) => {
        setCalls(prev => {
            if (!data) {
                const nextCalls = prev.filter(c => c.id !== editingEval?.id);
                if (selectedOperator?.id) {
                    callsCacheRef.current.set(getCallsCacheKey(selectedOperator.id, selectedMonth), nextCalls);
                }
                return nextCalls;
            }
            const newCall = {
                id: data.id, fileName: `Call ${data.phoneNumber}`, scores: data.scores, criterionComments: data.criterionComments,
                combinedComment: data.comment, totalScore: data.totalScore, date: new Date().toISOString().slice(0,10),
                audioUrl: data.audioUrl, isDraft: data.isDraft, selectedDirection: data.selectedDirection,
                directionId: data.directionId ?? selectedOperator?.direction_id, directions: data.directions,
                phoneNumber: data.phoneNumber, assignedMonth: data.assignedMonth, isCorrection: data.isCorrection,
                appeal_date: data.appeal_date, is_imported: false,
                sv_request: false, sv_request_approved: false, _rawEvaluation: {}
            };
            let updated = data.isCorrection
                ? prev.filter(c => c.id !== data.id && c.id !== editingEval?.id)
                : prev.filter(c => c.id !== newCall.id);
            const nextCalls = [...updated, newCall];
            if (selectedOperator?.id) {
                callsCacheRef.current.set(getCallsCacheKey(selectedOperator.id, selectedMonth), nextCalls);
            }
            return nextCalls;
        });
        if (data?.isCorrection && data?.id) { delete audioUrlCache[data.id]; if (editingEval?.id) delete audioUrlCache[editingEval.id]; }
        setEditingEval(null);
        if (!data?.isDraft) fetchEvaluations({ force: true });
    };

    const handleSelectCall = async (callId) => {
        const call = calls.find(c => c.id === callId);
        if (!call) return;
        if (call.isDraft) { setEditingEval(call); setShowEvalModal(true); return; }
        if (expandedId !== callId) {
            setLoadingCallId(callId);
            if (!call.audioUrl && call.directions?.[0]?.hasFileUpload) {
                const url = await getAudioUrl(call.id, userId);
                if (url) {
                    setCalls(prev => {
                        const nextCalls = prev.map(c => c.id===callId ? {...c, audioUrl:url} : c);
                        if (selectedOperator?.id) {
                            callsCacheRef.current.set(getCallsCacheKey(selectedOperator.id, selectedMonth), nextCalls);
                        }
                        return nextCalls;
                    });
                }
            }
            setExpandedId(callId);
            setLoadingCallId(null);
        } else setExpandedId(null);
    };

    const deleteImportedCall = async (id) => {
        if (!confirm('Удалить импортированный звонок?')) return;
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluations/${id}`, { method:'DELETE', headers:{'Content-Type':'application/json','X-User-Id':userId} });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error);
            setCalls(prev => {
                const nextCalls = prev.filter(c => c.id !== id);
                if (selectedOperator?.id) {
                    callsCacheRef.current.set(getCallsCacheKey(selectedOperator.id, selectedMonth), nextCalls);
                }
                return nextCalls;
            });
        } catch(e) { alert('Ошибка: ' + e.message); }
    };

    const callsByMonth = calls.filter(c => c.assignedMonth === selectedMonth);
    let displayedCalls = callsByMonth;
    if (viewMode === 'normal') displayedCalls = displayedCalls.filter(c => (!fromDate||c.date>=fromDate) && (!toDate||c.date<=toDate));
    else displayedCalls = displayedCalls.filter(c => c.date.slice(0,7) !== selectedMonth);

    const hasExtra = callsByMonth.filter(c => c.date.slice(0,7) !== selectedMonth).length > 0;
    const evalCount = displayedCalls.filter(c => !c.isDraft && !c.is_imported).length;
    const avgScore = evalCount > 0 ? displayedCalls.filter(c=>!c.isDraft&&!c.is_imported).reduce((s,c)=>s+parseFloat(c.totalScore),0)/evalCount : 0;
    const isMaxReached = callsByMonth.filter(c=>!c.isDraft&&!c.is_imported).length >= MAX_EVALS;
    const orderedSupervisors = [...supervisors].sort((a, b) => {
        const firedDiff = Number(isFiredStatus(a?.status)) - Number(isFiredStatus(b?.status));
        return firedDiff !== 0 ? firedDiff : compareByNameRu(a, b);
    });
    const orderedOperators = [...operators].sort((a, b) => {
        const firedDiff = Number(isFiredStatus(a?.status)) - Number(isFiredStatus(b?.status));
        return firedDiff !== 0 ? firedDiff : compareByNameRu(a, b);
    });
    const selectedSupervisorObj = selectedSupervisor ? supervisors.find(sv => sv.id === selectedSupervisor) : null;
    const selectedSupervisorIsFired = isFiredStatus(selectedSupervisorObj?.status);
    const selectedOperatorIsFired = isFiredStatus(selectedOperator?.status);

    const getScoreClass = (s) => {
        const v = parseFloat(s);
        if (isNaN(v)) return '';
        if (v >= 80) return 'score-high';
        if (v >= 60) return 'score-mid';
        return 'score-low';
    };

    return (
        <div className="app">
            {/* Header */}
            <header className="header">
                <div className="header-logo">
                    <div className="header-logo-dot" />
                    <h1>Журнал Оценок</h1>
                </div>
                <div className="header-right">
                    {userName && <span className="header-user">{userName}</span>}
                </div>
            </header>

            {/* Main panel */}
            <div className="main-panel">
                {/* Panel header with filters */}
                <div className="panel-header">
                    <span className="panel-title">Журнал оценок</span>
                    <div className="filters">
                        {userRole === 'admin' && (
                            <div className="filter-group">
                                <label className="label">Супервайзер</label>
                                <select className="select" value={selectedSupervisor||''} style={selectedSupervisorIsFired ? { color:'var(--text-3)' } : undefined} onChange={e => { setSelectedSupervisor(parseInt(e.target.value)||null); setSelectedOperator(null); setCalls([]); setExpandedId(null); }}>
                                    <option value="">Выбрать</option>
                                    {orderedSupervisors.map(sv => (
                                        <option key={sv.id} value={sv.id} className={isFiredStatus(sv?.status) ? 'option-fired' : ''} style={isFiredStatus(sv?.status) ? { color:'var(--text-3)' } : undefined}>{sv.name}</option>
                                    ))}
                                </select>
                            </div>
                        )}
                        <div className="filter-group">
                            <label className="label">Оператор</label>
                            <select className="select" value={selectedOperator?.id||''} style={selectedOperatorIsFired ? { color:'var(--text-3)' } : undefined} onChange={e => { const op=operators.find(o=>o.id===parseInt(e.target.value))||null; setSelectedOperator(op); setCalls([]); setExpandedId(null); }}>
                                <option value="">Выбрать</option>
                                {orderedOperators.map(op => (
                                    <option key={op.id} value={op.id} className={isFiredStatus(op?.status) ? 'option-fired' : ''} style={isFiredStatus(op?.status) ? { color:'var(--text-3)' } : undefined}>{op.name}</option>
                                ))}
                            </select>
                        </div>
                        <div className="filter-group">
                            <label className="label">Месяц</label>
                            <select className="select" value={selectedMonth} onChange={e => setSelectedMonth(e.target.value)}>
                                {months.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                            </select>
                        </div>
                        {userRole === 'admin' && (() => {
                            const lastDay = new Date(parseInt(selectedMonth.slice(0,4)), parseInt(selectedMonth.slice(5,7)), 0).getDate();
                            return <DateRangePicker minDate={`${selectedMonth}-01`} maxDate={`${selectedMonth}-${String(lastDay).padStart(2,'0')}`} setFromDate={setFromDate} setToDate={setToDate} />;
                        })()}
                    </div>
                </div>

                {/* Stats bar */}
                {selectedOperator && (
                    <div className="stats-bar">
                        <div className="stat-item">
                            <div className="stat-icon blue"><i className="fas fa-headset" /></div>
                            <div>
                                <div className="stat-value">{evalCount} <span style={{fontSize:12,color:'var(--text-2)',fontFamily:'var(--font)',fontWeight:400}}>/ {MAX_EVALS}</span></div>
                                <div className="stat-label">Оценок в месяце</div>
                            </div>
                        </div>
                        <div className="stat-item">
                            <div className="stat-icon green"><i className="fas fa-chart-line" /></div>
                            <div>
                                <div className="stat-value" style={{color: avgScore >= 80 ? 'var(--green)' : avgScore >= 60 ? 'var(--amber)' : avgScore > 0 ? 'var(--red)' : 'var(--text)'}}>
                                    {evalCount > 0 ? avgScore.toFixed(1) : '—'}
                                </div>
                                <div className="stat-label">Средний балл</div>
                            </div>
                        </div>
                        <div className="stat-item" style={{flex: 2}}>
                            <div style={{width:'100%'}}>
                                <div style={{display:'flex',justifyContent:'space-between',fontSize:11,color:'var(--text-2)',marginBottom:4}}>
                                    <span>Прогресс оценок</span>
                                    <span style={{fontFamily:'var(--font-mono)'}}>{evalCount}/{MAX_EVALS}</span>
                                </div>
                                <div style={{background:'var(--surface-2)',borderRadius:4,height:6,overflow:'hidden'}}>
                                    <div style={{height:'100%', borderRadius:4, background: evalCount/MAX_EVALS > 0.8 ? 'var(--green)' : 'var(--accent)', width:`${Math.min(evalCount/MAX_EVALS*100,100)}%`, transition:'width 0.4s ease'}} />
                                </div>
                            </div>
                        </div>
                    </div>
                )}

                {/* Table */}
                <div className="table-wrap">
                    {isLoading ? (
                        <table>
                            <thead><tr><th>#</th><th>Статус</th><th>Направление</th><th>Телефон</th><th>Балл</th><th>Дата обращения</th><th>Дата оценки</th></tr></thead>
                            <tbody>
                                {[...Array(5)].map((_,i) => (
                                    <tr key={i}><td colSpan={7}><div style={{display:'grid',gridTemplateColumns:'40px 80px 1fr 120px 60px 140px 1fr',gap:8,padding:'12px 16px 12px 20px'}}>
                                        {[40,80,'1fr',120,60,140,'1fr'].map((w,j)=><div key={j} className="skeleton" style={{height:16,width:typeof w==='number'?w:'100%'}} />)}
                                    </div></td></tr>
                                ))}
                            </tbody>
                        </table>
                    ) : displayedCalls.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-icon"><i className="fas fa-inbox" /></div>
                            <h3>Нет оценок</h3>
                            <p>Нет данных за {months.find(m=>m.value===selectedMonth)?.label || selectedMonth}{selectedOperator ? ` для ${selectedOperator.name}` : ''}. Добавьте первую оценку.</p>
                        </div>
                    ) : (
                        <table>
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Статус</th>
                                    <th>Направление</th>
                                    <th>Телефон</th>
                                    <th>Балл</th>
                                    <th>Дата обращения</th>
                                    <th>Дата оценки / Действия</th>
                                </tr>
                            </thead>
                            <tbody>
                                {displayedCalls.map((call, idx) => (
                                    <React.Fragment key={call.id}>
                                        <tr
                                            className={`${!call.is_imported ? 'clickable' : ''} ${call.is_imported ? 'imported' : ''} ${expandedId===call.id ? 'expanded' : ''}`}
                                            onClick={!call.is_imported ? () => handleSelectCall(call.id) : undefined}
                                        >
                                            <td>{idx+1}</td>
                                            <td>
                                                {loadingCallId === call.id ? (
                                                    <span style={{display:'flex',alignItems:'center',gap:6,fontSize:12,color:'var(--text-2)'}}>
                                                        <div style={{width:12,height:12,border:'2px solid var(--border-strong)',borderTopColor:'var(--accent)',borderRadius:'50%',animation:'spin 0.6s linear infinite'}} /> Загрузка
                                                    </span>
                                                ) : (
                                                    <span className={`badge ${call.is_imported ? 'badge-amber' : call.isDraft ? 'badge-blue' : 'badge-green'}`}>
                                                        <span className="badge-dot" />
                                                        {call.is_imported ? 'Не оценён' : call.isDraft ? 'Черновик' : 'Оценён'}
                                                    </span>
                                                )}
                                            </td>
                                            <td style={{color:'var(--text-2)'}}>{call.selectedDirection || '—'}</td>
                                            <td style={{fontFamily:'var(--font-mono)',fontSize:12}}>{call.phoneNumber || '—'}</td>
                                            <td>
                                                {call.totalScore != null ? (
                                                    <span className={`score-chip ${getScoreClass(call.totalScore)}`}>{Math.round(call.totalScore)}</span>
                                                ) : <span style={{color:'var(--text-3)'}}>—</span>}
                                            </td>
                                            <td style={{fontSize:12,color:'var(--text-2)'}}>{fmtDate(call.appeal_date)}</td>
                                            <td>
                                                <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8}}>
                                                    <span style={{fontSize:12,color:'var(--text-2)'}}>{fmtDate(call._rawEvaluation?.evaluation_date||call.date)}</span>
                                                    <div style={{display:'flex',alignItems:'center',gap:6,flexShrink:0}} onClick={e=>e.stopPropagation()}>
                                                        {call.is_imported ? (
                                                            <>
                                                                <button className="btn btn-green btn-sm" onClick={() => { setEditingEval(call); setShowEvalModal(true); }}><i className="fas fa-star" /> Оценить</button>
                                                                {userRole==='admin' && <button className="btn btn-danger btn-sm" onClick={() => deleteImportedCall(call.id)}><i className="fas fa-trash" /></button>}
                                                            </>
                                                        ) : (
                                                            <>
                                                                <SvRequestButton call={call} userId={userId} userRole={userRole} fetchEvaluations={fetchEvaluations} onReevaluate={() => { setEditingEval({...call,isReevaluation:true}); setShowEvalModal(true); }} />
                                                                {userRole==='admin' && !call.isDraft && (
                                                                    <>
                                                                        <button className="btn btn-secondary btn-sm" onClick={() => { setEditingEval({...call,isReevaluation:true}); setShowEvalModal(true); }}>
                                                                            <i className="fas fa-redo" /> Переоценить
                                                                        </button>
                                                                        {call.isCorrection && (
                                                                            <button className="btn btn-secondary btn-sm" onClick={async () => {
                                                                                try {
                                                                                    const r = await authFetch(`${API_BASE_URL}/api/call_versions/${call.id}`, {headers:{'X-User-Id':userId}});
                                                                                    const d = await r.json();
                                                                                    if (d.status==='success') { setVersionHistory(d.versions); setShowVersionsModal(true); }
                                                                                } catch(e) { console.error(e); }
                                                                            }}>
                                                                                <i className="fas fa-history" />
                                                                            </button>
                                                                        )}
                                                                    </>
                                                                )}
                                                            </>
                                                        )}
                                                    </div>
                                                </div>
                                            </td>
                                        </tr>

                                        {/* Expanded row */}
                                        {expandedId === call.id && (
                                            <tr className="expanded-row">
                                                <td colSpan={7}>
                                                    <div className="expanded-content">
                                                        <h4>Детали оценки</h4>
                                                        <div className="expanded-meta">
                                                            <div className="expanded-meta-item"><strong>Оценщик:</strong> {call._rawEvaluation?.evaluator || '—'}</div>
                                                            <div className="expanded-meta-item"><strong>Дата оценки:</strong> {fmtDate(call._rawEvaluation?.evaluation_date||call.date)}</div>
                                                            <div className="expanded-meta-item"><strong>Дата обращения:</strong> {fmtDate(call._rawEvaluation?.appeal_date||call.appeal_date)}</div>
                                                        </div>
                                                        {call.audioUrl && call.directions?.[0]?.hasFileUpload && (
                                                            <div className="audio-wrap" style={{marginBottom:14,maxWidth:480}}>
                                                                <div className="audio-label">Аудиозапись</div>
                                                                <audio controls style={{width:'100%'}}><source src={call.audioUrl} type="audio/mpeg" /></audio>
                                                            </div>
                                                        )}
                                                        {call.directions?.[0]?.criteria?.length > 0 && (
                                                            <table className="crit-table">
                                                                <thead><tr><th>Критерий</th><th>Вес</th><th>Оценка</th><th>Комментарий</th></tr></thead>
                                                                <tbody>
                                                                    {call.directions[0].criteria.map((c, ci) => (
                                                                        <tr key={ci}>
                                                                            <td>{c.name}</td>
                                                                            <td style={{fontFamily:'var(--font-mono)',fontSize:12}}>{c.isCritical ? 'Крит.' : c.weight}</td>
                                                                            <td>
                                                                                <span className={call.scores[ci]==='Correct'||call.scores[ci]==='N/A' ? 'score-correct' : 'score-error'}>
                                                                                    {call.scores[ci] || 'Correct'}
                                                                                </span>
                                                                            </td>
                                                                            <td style={{color:'var(--text-2)',fontSize:12}}>{call.criterionComments?.[ci] || '—'}</td>
                                                                        </tr>
                                                                    ))}
                                                                </tbody>
                                                            </table>
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                        )}
                                    </React.Fragment>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>

                {/* Panel footer */}
                <div className="panel-footer">
                    <span className="panel-footer-info">
                        {selectedOperator ? `${displayedCalls.length} записей · ${selectedOperator.name} · ${months.find(m=>m.value===selectedMonth)?.label}` : 'Выберите оператора'}
                    </span>
                    <div style={{display:'flex',gap:8}}>
                        {userRole==='admin' && (viewMode==='extra'||hasExtra) && (
                            <button className="btn btn-secondary btn-sm" onClick={() => setViewMode(v=>v==='normal'?'extra':'normal')}>
                                <i className={`fas fa-${viewMode==='normal'?'filter':'list'}`} /> {viewMode==='normal' ? 'Доп. оценки' : 'Основные'}
                            </button>
                        )}
                        {viewMode === 'normal' && (
                            <button
                                className={`btn btn-primary btn-sm ${(!selectedOperator||isMaxReached) ? 'disabled' : ''}`}
                                style={{opacity:(!selectedOperator||isMaxReached)?0.4:1,cursor:(!selectedOperator||isMaxReached)?'not-allowed':'pointer'}}
                                onClick={() => { if (!selectedOperator||isMaxReached) return; setEditingEval(null); setShowEvalModal(true); }}
                                disabled={!selectedOperator||isMaxReached}
                            >
                                <i className="fas fa-plus" /> Добавить оценку
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {/* Modals */}
            <EvaluationModal
                isOpen={showEvalModal}
                onClose={() => { setShowEvalModal(false); setEditingEval(null); }}
                onSubmit={handleEvaluateCall}
                directions={directions}
                operator={selectedOperator}
                selectedMonth={selectedMonth}
                userId={userId}
                userName={userName}
                existingEvaluation={editingEval}
            />

            {/* Version history modal */}
            {showVersionsModal && (
                <div className="modal-backdrop" onClick={() => setShowVersionsModal(false)}>
                    <div className="modal" style={{maxWidth:640}} onClick={e=>e.stopPropagation()}>
                        <div className="modal-header">
                            <div><h2>История версий</h2><div className="modal-header-sub">Все редакции данной оценки</div></div>
                            <button className="close-btn" onClick={() => setShowVersionsModal(false)}><i className="fas fa-times" /></button>
                        </div>
                        <div className="modal-body">
                            {versionHistory.map((v, i) => (
                                <div key={i} className="version-item">
                                    <div className="version-item-header">
                                        <span className="version-badge">Версия {versionHistory.length - i}</span>
                                        <span style={{fontSize:12,color:'var(--text-2)',fontFamily:'var(--font-mono)'}}>{v.evaluation_date?.split('T')[0]}</span>
                                    </div>
                                    <div className="version-grid">
                                        <div><strong>{v.score}</strong>Балл</div>
                                        <div><strong>{v.evaluator_name}</strong>Оценщик</div>
                                        <div><strong>{v.phone_number}</strong>Телефон</div>
                                        <div><strong>{v.month}</strong>Месяц</div>
                                        <div><strong>{v.appeal_date||'—'}</strong>Дата обращения</div>
                                    </div>
                                    {v.comment && <div style={{marginTop:10,fontSize:12,color:'var(--text-2)',padding:'8px',background:'var(--surface-2)',borderRadius:'var(--radius)'}}>{v.comment}</div>}
                                    {v.audio_path && <audio controls style={{width:'100%',marginTop:10}}><source src={v.audio_url||''} type="audio/mpeg" /></audio>}
                                </div>
                            ))}
                        </div>
                        <div className="modal-footer">
                            <button className="btn btn-secondary" onClick={() => setShowVersionsModal(false)}>Закрыть</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

const rootEl = document.getElementById('root');
const root = rootEl ? createRoot(rootEl) : null;

const renderApp = ({ user = null, initialSelection = null } = {}) => {
    if (!root) return;
    root.render(<App user={user} initialSelection={initialSelection} />);
};

const isEmbedded = window.parent && window.parent !== window;
if (isEmbedded) {
    document.body.classList.add('embedded-mode');
}

if (isEmbedded) {
    const onMessage = (event) => {
        if (event.origin !== window.location.origin) return;
        const data = event.data || {};
        if (data.type !== 'CALL_EVALUATION_INIT') return;
        const nextState = { user: data.user || null, initialSelection: data.initialSelection || null };
        writeEmbedState(nextState);
        renderApp(nextState);
    };

    window.addEventListener('message', onMessage);
    window.parent.postMessage({ type: 'CALL_EVALUATION_READY' }, window.location.origin);
    renderApp(readEmbedState() || {});
} else {
    const storedState = readEmbedState();
    const nextState = {
        user: window.__CALL_EVALUATION_USER__ || storedState?.user || null,
        initialSelection: window.__CALL_EVALUATION_SELECTION__ || storedState?.initialSelection || null
    };
    if (nextState.user || nextState.initialSelection) writeEmbedState(nextState);
    renderApp(nextState);
}
