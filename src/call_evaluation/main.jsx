import '../staleBundleRecovery';
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createRoot } from 'react-dom/client';
import { createPortal } from 'react-dom';
import './styles.css';
import FaIcon from '../components/common/FaIcon';
const API_BASE_URL = 'https://otp-2-fos4.onrender.com';
const AUTH_REFRESH_URL = `${API_BASE_URL}/api/auth/refresh`;
const EMBED_STATE_KEY = 'call_evaluation_embed_state';
const AUTH_TRANSPORT_STORAGE_KEY = 'otp_auth_transport';
const ACCESS_TOKEN_STORAGE_KEY = 'otp_access_token';
const REFRESH_TOKEN_STORAGE_KEY = 'otp_refresh_token';
let refreshPromise = null;
const audioUrlCache = {};
const authRuntimeState = {
    transport: null,
    accessToken: '',
    refreshToken: ''
};

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

const normalizeClientAuthTransport = (value) => {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'bearer' || normalized === 'cookie') return normalized;
    return null;
};

const safeGetBrowserStorage = (storageName) => {
    if (typeof window === 'undefined') return null;
    try {
        const storage = window[storageName];
        if (!storage) return null;
        storage.getItem('__otp_storage_probe__');
        return storage;
    } catch {
        return null;
    }
};

const safeStorageGetItem = (storage, key) => {
    if (!storage) return '';
    try {
        return String(storage.getItem(key) || '').trim();
    } catch {
        return '';
    }
};

const safeStorageSetItem = (storage, key, value) => {
    if (!storage) return false;
    try {
        storage.setItem(key, value);
        return true;
    } catch {
        return false;
    }
};

const safeStorageRemoveItem = (storage, key) => {
    if (!storage) return;
    try {
        storage.removeItem(key);
    } catch {}
};

const isLikelyCookieRestrictedMobileContext = () => {
    if (typeof window === 'undefined' || typeof navigator === 'undefined') return false;
    const userAgent = String(navigator.userAgent || '').toLowerCase();
    const isMobileDevice = /(android|iphone|ipad|ipod|iemobile|opera mini|mobile|windows phone)/i.test(userAgent);
    const isEmbeddedWebView = /\bwv\b|; wv\)|fbav|fban|instagram|line\/|tgweb|telegrambot/i.test(userAgent);
    return isMobileDevice || isEmbeddedWebView;
};

const isCrossOriginApiContext = () => {
    if (typeof window === 'undefined') return false;
    try {
        const apiUrl = new URL(API_BASE_URL, window.location.origin);
        return apiUrl.origin !== window.location.origin;
    } catch {
        return true;
    }
};

const shouldForceBearerAuthTransport = () => {
    return isCrossOriginApiContext() || isLikelyCookieRestrictedMobileContext();
};

const shouldUseLegacyMobileBearerStorage = () => {
    return isLikelyCookieRestrictedMobileContext();
};

const resolveRuntimeTokenField = (storageKey) => {
    if (storageKey === ACCESS_TOKEN_STORAGE_KEY) return 'accessToken';
    if (storageKey === REFRESH_TOKEN_STORAGE_KEY) return 'refreshToken';
    return null;
};

const getStoredAuthTransport = () => {
    const runtimeTransport = normalizeClientAuthTransport(authRuntimeState.transport);
    if (runtimeTransport) return runtimeTransport;

    const sessionStorageRef = safeGetBrowserStorage('sessionStorage');
    const sessionTransport = normalizeClientAuthTransport(
        safeStorageGetItem(sessionStorageRef, AUTH_TRANSPORT_STORAGE_KEY)
    );
    if (sessionTransport) {
        authRuntimeState.transport = sessionTransport;
        return sessionTransport;
    }

    const localStorageRef = safeGetBrowserStorage('localStorage');
    const localTransport = normalizeClientAuthTransport(
        safeStorageGetItem(localStorageRef, AUTH_TRANSPORT_STORAGE_KEY)
    );
    if (localTransport) {
        authRuntimeState.transport = localTransport;
        safeStorageSetItem(sessionStorageRef, AUTH_TRANSPORT_STORAGE_KEY, localTransport);
        return localTransport;
    }
    return null;
};

const setStoredAuthTransport = (transport) => {
    const normalized = normalizeClientAuthTransport(transport);
    const sessionStorageRef = safeGetBrowserStorage('sessionStorage');
    const localStorageRef = safeGetBrowserStorage('localStorage');
    authRuntimeState.transport = normalized;

    if (!normalized) {
        safeStorageRemoveItem(sessionStorageRef, AUTH_TRANSPORT_STORAGE_KEY);
        safeStorageRemoveItem(localStorageRef, AUTH_TRANSPORT_STORAGE_KEY);
        return;
    }
    safeStorageSetItem(sessionStorageRef, AUTH_TRANSPORT_STORAGE_KEY, normalized);
    safeStorageSetItem(localStorageRef, AUTH_TRANSPORT_STORAGE_KEY, normalized);
};

const getStoredAuthToken = (storageKey) => {
    const runtimeField = resolveRuntimeTokenField(storageKey);
    const runtimeToken = runtimeField ? String(authRuntimeState[runtimeField] || '').trim() : '';
    if (runtimeToken) return runtimeToken;

    const sessionStorageRef = safeGetBrowserStorage('sessionStorage');
    const localStorageRef = safeGetBrowserStorage('localStorage');

    if (shouldUseLegacyMobileBearerStorage()) {
        const mobileLocalToken = safeStorageGetItem(localStorageRef, storageKey);
        if (mobileLocalToken) {
            if (runtimeField) authRuntimeState[runtimeField] = mobileLocalToken;
            return mobileLocalToken;
        }
        const mobileSessionToken = safeStorageGetItem(sessionStorageRef, storageKey);
        if (mobileSessionToken) {
            if (runtimeField) authRuntimeState[runtimeField] = mobileSessionToken;
            return mobileSessionToken;
        }
        return '';
    }

    const sessionToken = safeStorageGetItem(sessionStorageRef, storageKey);
    if (sessionToken) {
        if (runtimeField) authRuntimeState[runtimeField] = sessionToken;
        return sessionToken;
    }

    const legacyToken = safeStorageGetItem(localStorageRef, storageKey);
    if (legacyToken) {
        if (runtimeField) authRuntimeState[runtimeField] = legacyToken;
        if (safeStorageSetItem(sessionStorageRef, storageKey, legacyToken)) {
            safeStorageRemoveItem(localStorageRef, storageKey);
        }
        return legacyToken;
    }
    return '';
};

const hasStoredBearerTokens = () => {
    return Boolean(
        getStoredAuthToken(ACCESS_TOKEN_STORAGE_KEY) &&
        getStoredAuthToken(REFRESH_TOKEN_STORAGE_KEY)
    );
};

const clearStoredBearerTokens = () => {
    authRuntimeState.accessToken = '';
    authRuntimeState.refreshToken = '';
    const sessionStorageRef = safeGetBrowserStorage('sessionStorage');
    const localStorageRef = safeGetBrowserStorage('localStorage');
    safeStorageRemoveItem(sessionStorageRef, ACCESS_TOKEN_STORAGE_KEY);
    safeStorageRemoveItem(sessionStorageRef, REFRESH_TOKEN_STORAGE_KEY);
    safeStorageRemoveItem(localStorageRef, ACCESS_TOKEN_STORAGE_KEY);
    safeStorageRemoveItem(localStorageRef, REFRESH_TOKEN_STORAGE_KEY);
};

const clearAuthTokens = () => {
    clearStoredBearerTokens();
    authRuntimeState.transport = null;
    const sessionStorageRef = safeGetBrowserStorage('sessionStorage');
    const localStorageRef = safeGetBrowserStorage('localStorage');
    safeStorageRemoveItem(sessionStorageRef, AUTH_TRANSPORT_STORAGE_KEY);
    safeStorageRemoveItem(localStorageRef, AUTH_TRANSPORT_STORAGE_KEY);
};

const getPreferredAuthTransport = () => {
    if (hasStoredBearerTokens()) return 'bearer';
    if (shouldForceBearerAuthTransport()) return 'bearer';
    const storedTransport = getStoredAuthTransport();
    if (storedTransport) return storedTransport;
    return 'cookie';
};

const activateCookieAuthTransport = () => {
    clearStoredBearerTokens();
    setStoredAuthTransport('cookie');
};

const persistBearerAuthTokens = (payload) => {
    const accessToken = String(payload?.access_token || '').trim();
    const refreshToken = String(payload?.refresh_token || '').trim();
    if (!accessToken || !refreshToken) return false;

    authRuntimeState.accessToken = accessToken;
    authRuntimeState.refreshToken = refreshToken;

    const sessionStorageRef = safeGetBrowserStorage('sessionStorage');
    const localStorageRef = safeGetBrowserStorage('localStorage');

    if (shouldUseLegacyMobileBearerStorage()) {
        safeStorageSetItem(localStorageRef, ACCESS_TOKEN_STORAGE_KEY, accessToken);
        safeStorageSetItem(localStorageRef, REFRESH_TOKEN_STORAGE_KEY, refreshToken);
        safeStorageSetItem(sessionStorageRef, ACCESS_TOKEN_STORAGE_KEY, accessToken);
        safeStorageSetItem(sessionStorageRef, REFRESH_TOKEN_STORAGE_KEY, refreshToken);
        setStoredAuthTransport('bearer');
        return true;
    }

    const accessPersistedToSession = safeStorageSetItem(sessionStorageRef, ACCESS_TOKEN_STORAGE_KEY, accessToken);
    const refreshPersistedToSession = safeStorageSetItem(sessionStorageRef, REFRESH_TOKEN_STORAGE_KEY, refreshToken);
    if (accessPersistedToSession) safeStorageRemoveItem(localStorageRef, ACCESS_TOKEN_STORAGE_KEY);
    else safeStorageSetItem(localStorageRef, ACCESS_TOKEN_STORAGE_KEY, accessToken);
    if (refreshPersistedToSession) safeStorageRemoveItem(localStorageRef, REFRESH_TOKEN_STORAGE_KEY);
    else safeStorageSetItem(localStorageRef, REFRESH_TOKEN_STORAGE_KEY, refreshToken);
    setStoredAuthTransport('bearer');
    return true;
};

const stripLegacyAuthHeaders = (headers = {}) => {
    const nextHeaders = { ...(headers || {}) };
    delete nextHeaders['Authorization'];
    delete nextHeaders['authorization'];
    delete nextHeaders['X-Refresh-Token'];
    delete nextHeaders['x-refresh-token'];
    return nextHeaders;
};

const resolveAuthTransportFromHeaders = (headers = {}) => {
    if (!headers || typeof headers !== 'object') return null;
    return (
        normalizeClientAuthTransport(headers['X-Auth-Transport']) ||
        normalizeClientAuthTransport(headers['x-auth-transport'])
    );
};

const withAccessTokenHeader = (headers = {}, options = {}) => {
    const { includeRefreshToken = false, transportOverride = null } = options || {};
    const nextHeaders = stripLegacyAuthHeaders(headers);
    const authTransport =
        normalizeClientAuthTransport(transportOverride) ||
        resolveAuthTransportFromHeaders(headers) ||
        getPreferredAuthTransport();
    nextHeaders['X-Auth-Transport'] = authTransport;

    if (authTransport === 'bearer') {
        const accessToken = getStoredAuthToken(ACCESS_TOKEN_STORAGE_KEY);
        const refreshToken = includeRefreshToken ? getStoredAuthToken(REFRESH_TOKEN_STORAGE_KEY) : '';
        if (accessToken) nextHeaders.Authorization = `Bearer ${accessToken}`;
        if (refreshToken) nextHeaders['X-Refresh-Token'] = refreshToken;
    }

    return nextHeaders;
};

const persistRotatedBearerTokens = (response, data = null) => {
    const payload = data && typeof data === 'object' ? data : {};
    const newAccessToken =
        response?.headers?.get('x-new-access-token') ||
        response?.headers?.get('X-New-Access-Token') ||
        payload.access_token;
    const newRefreshToken =
        response?.headers?.get('x-new-refresh-token') ||
        response?.headers?.get('X-New-Refresh-Token') ||
        payload.refresh_token;
    if (!newAccessToken && !newRefreshToken) return;

    persistBearerAuthTokens({
        access_token: newAccessToken || getStoredAuthToken(ACCESS_TOKEN_STORAGE_KEY),
        refresh_token: newRefreshToken || getStoredAuthToken(REFRESH_TOKEN_STORAGE_KEY)
    });
};

const isRecoverableAuthError = (body = null) => {
    const code = body?.code;
    const apiErrorText = body?.error;
    return (
        code === 'TOKEN_EXPIRED' ||
        code === 'INVALID_TOKEN' ||
        code === 'INVALID_TOKEN_TYPE' ||
        code === 'MISSING_TOKEN' ||
        code === 'SESSION_EXPIRED' ||
        code === 'SESSION_NOT_FOUND' ||
        code === 'SESSION_REVOKED' ||
        apiErrorText === 'JWT authentication failed'
    );
};

const authFetch = async (url, opts = {}, retry = true) => {
    const requestHeaders = withAccessTokenHeader(opts.headers || {});
    const res = await fetch(url, {
        credentials: 'include',
        ...opts,
        headers: requestHeaders
    });

    const body = await readJsonSafe(res.clone());
    const transportFromResponse =
        normalizeClientAuthTransport(res.headers.get('x-auth-transport')) ||
        normalizeClientAuthTransport(body?.auth_transport);
    const resolvedTransport = shouldForceBearerAuthTransport()
        ? 'bearer'
        : (transportFromResponse || getPreferredAuthTransport());

    if (resolvedTransport === 'bearer') {
        persistRotatedBearerTokens(res, body);
    } else if (resolvedTransport === 'cookie') {
        activateCookieAuthTransport();
    }

    if (res.status !== 401 || !retry || !isRecoverableAuthError(body)) return res;

    if (!refreshPromise) {
        const refreshTransport = getPreferredAuthTransport();
        const refreshToken = refreshTransport === 'bearer'
            ? getStoredAuthToken(REFRESH_TOKEN_STORAGE_KEY)
            : '';

        refreshPromise = fetch(AUTH_REFRESH_URL, {
            method: 'POST',
            credentials: 'include',
            headers: withAccessTokenHeader(
                { 'Content-Type': 'application/json' },
                {
                    includeRefreshToken: true,
                    transportOverride: refreshTransport
                }
            ),
            body: JSON.stringify(
                refreshTransport === 'bearer'
                    ? {
                        auth_transport: 'bearer',
                        refresh_token: refreshToken || undefined
                    }
                    : {
                        auth_transport: 'cookie'
                    }
            )
        }).then(async (refreshResponse) => {
            const refreshData = await readJsonSafe(refreshResponse.clone());
            if (!refreshResponse.ok) {
                clearAuthTokens();
                return refreshResponse;
            }

            const refreshResolvedTransport = shouldForceBearerAuthTransport()
                ? 'bearer'
                : (
                    normalizeClientAuthTransport(refreshData?.auth_transport) ||
                    normalizeClientAuthTransport(refreshResponse.headers.get('x-auth-transport')) ||
                    getPreferredAuthTransport()
                );

            if (refreshResolvedTransport === 'bearer') {
                if (!persistBearerAuthTokens({
                    access_token:
                        refreshResponse.headers.get('x-new-access-token') ||
                        refreshData?.access_token,
                    refresh_token:
                        refreshResponse.headers.get('x-new-refresh-token') ||
                        refreshData?.refresh_token
                })) {
                    clearAuthTokens();
                    throw new Error('Bearer refresh succeeded without rotated tokens');
                }
            } else {
                activateCookieAuthTransport();
            }

            return refreshResponse;
        }).catch((refreshError) => {
            clearAuthTokens();
            throw refreshError;
        }).finally(() => {
            refreshPromise = null;
        });
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

const emitCallEvaluationToast = (message, type = 'info') => {
    const text = String(message ?? '');
    try {
        if (typeof window !== 'undefined' && typeof window.showToast === 'function') {
            window.showToast(text, type);
            return;
        }
    } catch (error) {
        console.warn('Failed to emit call-evaluation toast:', error);
    }
    if (type === 'error') console.error(text);
    else console.warn(text);
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

const normalizeCalibrationScore = (value) => {
    const raw = String(value ?? '').trim().toLowerCase();
    if (raw === 'correct') return 'Correct';
    if (raw === 'n/a' || raw === 'na' || raw === 'n\\a') return 'N/A';
    if (raw === 'incorrect') return 'Incorrect';
    if (raw === 'deficiency') return 'Deficiency';
    if (raw === 'error') return 'Error';
    return String(value ?? '').trim() || 'Correct';
};

const calibrationScoreLabel = (value) => {
    const v = normalizeCalibrationScore(value);
    if (v === 'Correct') return 'Корректно';
    if (v === 'N/A') return 'N/A';
    if (v === 'Incorrect') return 'Ошибка';
    if (v === 'Deficiency') return 'Недочёт';
    if (v === 'Error') return 'Критич. ошибка';
    return v || '—';
};

const normalizeStatus = (status) => String(status ?? '').trim().toLowerCase();
const isFiredStatus = (status) => {
    const s = normalizeStatus(status);
    return s === 'fired' || s === 'dismissed' || s === 'terminated' || s === 'уволен';
};
const compareByNameRu = (a, b) => String(a?.name ?? '').localeCompare(String(b?.name ?? ''), 'ru');
const sortByFiredAndName = (list = []) => [...list].sort((a, b) => {
    const firedDiff = Number(isFiredStatus(a?.status)) - Number(isFiredStatus(b?.status));
    return firedDiff !== 0 ? firedDiff : compareByNameRu(a, b);
});

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
                    <FaIcon className="fa-regular fa-comment-dots" />
                </button>
                <button className="crit-info-btn" onClick={onShowInfo} title="Описание критерия">
                    <FaIcon className="fa-regular fa-circle-question" />
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
            emitCallEvaluationToast('Заявка отправлена', 'success');
        } catch(e) { emitCallEvaluationToast('Ошибка: ' + e.message, 'error'); }
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
                            <button className="close-btn" onClick={() => setShowModal(false)}><FaIcon className="fas fa-times"/></button>
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
                <FaIcon className="fas fa-clock" style={{fontSize:11}} /> Ожидает
            </span>
        </HoverTooltip>
    );

    if (call.sv_request_approved) return (
        <div style={{display:'flex', alignItems:'center', gap:6}}>
            <HoverTooltip text={[call.sv_request_comment && `Комм.: ${call.sv_request_comment}`, call.sv_request_by_name && `Запросил: ${call.sv_request_by_name}`, call.sv_request_approved_by_name && `Одобрил: ${call.sv_request_approved_by_name}`].filter(Boolean).join('\n') || 'Запрос одобрен'}>
                <FaIcon className="fas fa-info-circle" style={{color:'var(--green)', cursor:'pointer'}} />
            </HoverTooltip>
            <button className="btn btn-primary btn-sm" onClick={e => { e.stopPropagation(); onReevaluate(); }}>Переоценить</button>
        </div>
    );
    return null;
};

const toDateInputValue = (value) => {
    if (!value) return '';
    const text = String(value).trim();
    const m = text.match(/^(\d{4}-\d{2}-\d{2})/);
    return m ? m[1] : '';
};

const toTimeInputValue = (value) => {
    if (!value) return '';
    const text = String(value).trim();
    const m = text.match(/^(\d{2}:\d{2})/);
    return m ? m[1] : '';
};

const FeedbackModal = ({
    isOpen,
    onClose,
    call,
    userId,
    onSaved
}) => {
    const [feedbackComment, setFeedbackComment] = useState('');
    const [deliveryComment, setDeliveryComment] = useState('');
    const [feedbackDate, setFeedbackDate] = useState('');
    const [startTime, setStartTime] = useState('');
    const [endTime, setEndTime] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);

    useEffect(() => {
        if (!isOpen) return;
        const currentFeedback = call?.feedback || null;
        const now = new Date();
        const defaultDate = now.toISOString().slice(0, 10);

        setFeedbackComment(String(currentFeedback?.feedback_comment || '').trim());
        setDeliveryComment(String(currentFeedback?.delivery_comment || '').trim());
        setFeedbackDate(toDateInputValue(currentFeedback?.date) || defaultDate);
        setStartTime(toTimeInputValue(currentFeedback?.start_time));
        setEndTime(toTimeInputValue(currentFeedback?.end_time));
    }, [isOpen, call]);

    if (!isOpen || !call) return null;

    const hasExistingFeedback = !!call?.feedback?.id;
    const isDisabled =
        isSubmitting ||
        !feedbackComment.trim() ||
        !deliveryComment.trim() ||
        !feedbackDate ||
        !startTime ||
        !endTime;

    const submit = async () => {
        if (isDisabled) return;
        if (endTime <= startTime) {
            emitCallEvaluationToast('Время окончания должно быть позже времени начала', 'error');
            return;
        }

        setIsSubmitting(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluations/${call.id}/feedback`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Id': userId
                },
                body: JSON.stringify({
                    feedback_comment: feedbackComment.trim(),
                    delivery_comment: deliveryComment.trim(),
                    date: feedbackDate,
                    start_time: startTime,
                    end_time: endTime
                })
            });
            const d = await r.json();
            if (!r.ok || d.status !== 'success') {
                throw new Error(d?.error || 'Не удалось сохранить обратную связь');
            }

            emitCallEvaluationToast(hasExistingFeedback ? 'Обратная связь обновлена' : 'Обратная связь добавлена', 'success');
            if (typeof onSaved === 'function') onSaved(d.feedback || null);
            onClose?.();
        } catch (e) {
            emitCallEvaluationToast(`Ошибка: ${e.message}`, 'error');
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="modal-backdrop" onClick={onClose}>
            <div className="modal request-modal" style={{maxWidth: 560}} onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <div>
                        <h2>Обратная связь</h2>
                        <div className="modal-header-sub">Call ID: {call.id}</div>
                    </div>
                    <button className="close-btn" onClick={onClose}><FaIcon className="fas fa-times" /></button>
                </div>
                <div className="modal-body">
                    <div className="field">
                        <label className="label">Обратная связь</label>
                        <textarea
                            className="textarea"
                            rows={3}
                            placeholder="Что было донесено оператору"
                            value={feedbackComment}
                            onChange={e => setFeedbackComment(e.target.value)}
                        />
                    </div>
                    <div className="field">
                        <label className="label">Как проведена обратная связь</label>
                        <textarea
                            className="textarea"
                            rows={3}
                            placeholder="Например: индивидуальный разбор, прослушивание звонка, чек-лист ошибок"
                            value={deliveryComment}
                            onChange={e => setDeliveryComment(e.target.value)}
                        />
                    </div>
                    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:10}}>
                        <div className="field" style={{marginBottom: 0}}>
                            <label className="label">Дата</label>
                            <input
                                className="input"
                                type="date"
                                value={feedbackDate}
                                onChange={e => setFeedbackDate(e.target.value)}
                            />
                        </div>
                        <div className="field" style={{marginBottom: 0}}>
                            <label className="label">Начало</label>
                            <input
                                className="input"
                                type="time"
                                value={startTime}
                                onChange={e => setStartTime(e.target.value)}
                            />
                        </div>
                        <div className="field" style={{marginBottom: 0}}>
                            <label className="label">Окончание</label>
                            <input
                                className="input"
                                type="time"
                                value={endTime}
                                onChange={e => setEndTime(e.target.value)}
                            />
                        </div>
                    </div>
                    <div style={{marginTop: 10, fontSize: 12, color:'var(--text-2)'}}>
                        При сохранении будет автоматически создан/обновлен тренинг с причиной
                        <strong style={{color:'var(--text)'}}> «Тренинг по качеству. Разбор ошибок»</strong>.
                    </div>
                </div>
                <div className="modal-footer">
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary" onClick={submit} disabled={isDisabled}>
                        {isSubmitting
                            ? <><span className="spinner" /> Сохранение...</>
                            : (hasExistingFeedback ? 'Обновить ОС' : 'Сохранить ОС')}
                    </button>
                </div>
            </div>
        </div>
    );
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
const EvaluationModal = ({
    isOpen,
    onClose,
    onSubmit,
    directions,
    operator,
    operators = [],
    supervisors = [],
    selectedSupervisorId = null,
    isAdminRole = false,
    isSupervisorRole = false,
    selectedMonth,
    userId,
    userName,
    existingEvaluation,
    submitMode = 'journal',
    calibrationRoomId = null,
    onCalibrationCallCreated = null
}) => {
    const [callFile, setCallFile] = useState(null);
    const [audioUrl, setAudioUrl] = useState(null);
    const [audioError, setAudioError] = useState(null);
    const [scores, setScores] = useState([]);
    const [comments, setComments] = useState([]);
    const [commentVisible, setCommentVisible] = useState([]);
    const [generalComment, setGeneralComment] = useState('');
    const [commentVisibleToOperator, setCommentVisibleToOperator] = useState(true);
    const [phoneNumber, setPhoneNumber] = useState('');
    const [phoneError, setPhoneError] = useState('');
    const [appealDate, setAppealDate] = useState('');
    const [assignedMonth, setAssignedMonth] = useState(selectedMonth);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [selectedDirId, setSelectedDirId] = useState(null);
    const [selectedCalibrationSupervisorId, setSelectedCalibrationSupervisorId] = useState(null);
    const [selectedCalibrationOperatorId, setSelectedCalibrationOperatorId] = useState(null);
    const [calibrationOperators, setCalibrationOperators] = useState([]);
    const [isCalibrationOperatorsLoading, setIsCalibrationOperatorsLoading] = useState(false);
    const [infoIndex, setInfoIndex] = useState(null);
    const [expectedDuration, setExpectedDuration] = useState(null);
    const [actualDuration, setActualDuration] = useState(null);
    const [durationMismatch, setDurationMismatch] = useState(false);
    const calibrationOperatorsCacheRef = useRef(new Map());
    const isLocked = !!(existingEvaluation?.isReevaluation || existingEvaluation?.is_imported);
    const isCalibrationAddCallMode = submitMode === 'calibration_add_call';
    const calibrationOperatorPool = isCalibrationAddCallMode ? calibrationOperators : operators;
    const activeOperator = isCalibrationAddCallMode
        ? (calibrationOperatorPool.find(op => op.id === selectedCalibrationOperatorId) || null)
        : operator;
    const canSelectCalibrationSupervisor = isAdminRole || (isSupervisorRole && supervisors.length > 0);
    const orderedCalibrationSupervisors = sortByFiredAndName(supervisors);
    const orderedCalibrationOperators = sortByFiredAndName(calibrationOperatorPool);
    const selectedCalibrationSupervisorObj = selectedCalibrationSupervisorId
        ? supervisors.find(sv => Number(sv.id) === Number(selectedCalibrationSupervisorId))
        : null;
    const selectedCalibrationSupervisorIsFired = isFiredStatus(selectedCalibrationSupervisorObj?.status);
    const selectedCalibrationOperatorObj = selectedCalibrationOperatorId
        ? calibrationOperatorPool.find(op => Number(op.id) === Number(selectedCalibrationOperatorId))
        : null;
    const selectedCalibrationOperatorIsFired = isFiredStatus(selectedCalibrationOperatorObj?.status);

    const currentDir = directions?.find(d => d.id === selectedDirId) || directions?.[0] || null;
    const criteria = currentDir?.criteria || [];
    const monthsRu = ['янв.','февр.','мар.','апр.','май','июн.','июл.','авг.','сент.','окт.','ноя.','дек.'];

    useEffect(() => {
        if (!isOpen || !isCalibrationAddCallMode) return;
        if (canSelectCalibrationSupervisor) {
            const hasUserInSupervisors = userId && supervisors.some((sv) => Number(sv.id) === Number(userId));
            const preferredSupervisorId = selectedSupervisorId || (hasUserInSupervisors ? userId : null) || supervisors?.[0]?.id || null;
            setSelectedCalibrationSupervisorId((prev) => {
                const hasCurrent = prev && supervisors.some((sv) => Number(sv.id) === Number(prev));
                return hasCurrent ? prev : preferredSupervisorId;
            });
            const primeSupervisorId = selectedSupervisorId || (hasUserInSupervisors ? userId : null);
            if (primeSupervisorId && Number(primeSupervisorId) === Number(preferredSupervisorId) && Array.isArray(operators)) {
                calibrationOperatorsCacheRef.current.set(String(primeSupervisorId), operators);
                setCalibrationOperators(operators);
            }
        } else {
            setSelectedCalibrationSupervisorId(userId || null);
            setCalibrationOperators(Array.isArray(operators) ? operators : []);
        }
    }, [
        isOpen,
        isCalibrationAddCallMode,
        canSelectCalibrationSupervisor,
        selectedSupervisorId,
        supervisors,
        operators,
        userId
    ]);

    useEffect(() => {
        if (!isOpen || !isCalibrationAddCallMode || !canSelectCalibrationSupervisor) return;
        if (!selectedCalibrationSupervisorId || !userId) {
            setCalibrationOperators([]);
            return;
        }

        const cacheKey = String(selectedCalibrationSupervisorId);
        const cached = calibrationOperatorsCacheRef.current.get(cacheKey);
        if (cached) {
            setCalibrationOperators(cached);
            return;
        }

        let cancelled = false;
        setIsCalibrationOperatorsLoading(true);
        authFetch(`${API_BASE_URL}/api/sv/data?id=${selectedCalibrationSupervisorId}`, { headers: { 'X-User-Id': userId } })
            .then(r => r.json())
            .then((d) => {
                if (cancelled) return;
                const nextOperators = d.status === 'success' ? (d.operators || []) : [];
                calibrationOperatorsCacheRef.current.set(cacheKey, nextOperators);
                setCalibrationOperators(nextOperators);
            })
            .catch(() => {
                if (!cancelled) setCalibrationOperators([]);
            })
            .finally(() => {
                if (!cancelled) setIsCalibrationOperatorsLoading(false);
            });

        return () => { cancelled = true; };
    }, [
        isOpen,
        isCalibrationAddCallMode,
        canSelectCalibrationSupervisor,
        selectedCalibrationSupervisorId,
        userId
    ]);

    useEffect(() => {
        if (!isOpen || !isCalibrationAddCallMode) return;
        const orderedOperators = sortByFiredAndName(calibrationOperatorPool);
        const operatorIdFromProp = operator?.id && orderedOperators.some(op => op.id === operator.id)
            ? operator.id
            : null;
        const preferredId = operatorIdFromProp || orderedOperators?.[0]?.id || null;
        const hasCurrent = selectedCalibrationOperatorId && orderedOperators.some(op => op.id === selectedCalibrationOperatorId);
        if (!hasCurrent) {
            setSelectedCalibrationOperatorId(preferredId);
        }
    }, [isOpen, isCalibrationAddCallMode, operator?.id, calibrationOperatorPool, selectedCalibrationOperatorId]);

    const MIN_TOL = 3, PCT_TOL = 0.15;
    const fmtSec = (s) => {
        if (!s) return '—';
        const t = Math.round(s), h = Math.floor(t/3600), m = Math.floor((t%3600)/60), sec = t%60;
        return h > 0 ? `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}` : `${m}:${String(sec).padStart(2,'0')}`;
    };

    useEffect(() => {
        if (!isOpen || !directions?.length) return;
        const modalOperator = isCalibrationAddCallMode
            ? (activeOperator || operator || calibrationOperatorPool?.[0] || null)
            : operator;
        if (isCalibrationAddCallMode && modalOperator?.id && selectedCalibrationOperatorId !== modalOperator.id) {
            setSelectedCalibrationOperatorId(modalOperator.id);
        }
        const initId = existingEvaluation
            ? (existingEvaluation.directionId ?? existingEvaluation._rawEvaluation?.direction_id ?? directions.find(d=>d.name===existingEvaluation.selectedDirection)?.id ?? modalOperator?.direction_id ?? directions[0]?.id)
            : (modalOperator?.direction_id ?? directions[0]?.id);
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
                setGeneralComment('');
                setCommentVisibleToOperator(true);
                setAssignedMonth(selectedMonth); setAudioUrl(null); setCallFile(null); setPhoneError('');
            } else {
                setScores(existingEvaluation.scores || []);
                setComments(existingEvaluation.criterionComments || []);
                setCommentVisible((existingEvaluation.criterionComments||[]).map(c=>!!(c&&c.trim())));
                setGeneralComment(
                    existingEvaluation.combinedComment
                    ?? existingEvaluation._rawEvaluation?.comment
                    ?? ''
                );
                setCommentVisibleToOperator(
                    existingEvaluation.commentVisibleToOperator
                    ?? existingEvaluation._rawEvaluation?.comment_visible_to_operator
                    ?? true
                );
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
            setGeneralComment('');
            setCommentVisibleToOperator(true);
            setCallFile(null); setAudioUrl(null); setPhoneNumber(''); setAppealDate(''); setPhoneError('');
            setAssignedMonth(selectedMonth);
        }
    }, [
        isOpen,
        existingEvaluation,
        directions,
        selectedMonth,
        operator,
        calibrationOperatorPool,
        activeOperator,
        isCalibrationAddCallMode,
        selectedCalibrationOperatorId
    ]);

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

    const isSubmitDisabled = !activeOperator || !currentDir || !criteria.length ||
        (isCalibrationAddCallMode && !calibrationRoomId) ||
        (currentDir?.hasFileUpload && !callFile && !audioUrl) ||
        scores.some((s,i) => (s==='Error'||s==='Incorrect') && !comments[i]?.trim()) ||
        durationMismatch;

    const handleSubmit = async (draft = false) => {
        setIsSubmitting(true);
        const fd = new FormData();
        const criteriaCommentSummary = criteria
            .map((c, i) => comments[i] ? `${c.name}: ${comments[i]}` : '')
            .filter(Boolean)
            .join('; ');
        const submitComment = isCalibrationAddCallMode
            ? criteriaCommentSummary
            : String(generalComment || '').trim();
        fd.append('evaluator', userName);
        fd.append('operator', activeOperator?.name || '');
        fd.append('phone_number', phoneNumber);
        fd.append('score', totalScore);
        fd.append('comment', submitComment);
        if (!isCalibrationAddCallMode) {
            fd.append('comment_visible_to_operator', String(!!commentVisibleToOperator));
        }
        fd.append('month', assignedMonth);
        fd.append('is_draft', draft);
        fd.append('scores', JSON.stringify(scores));
        fd.append('criterion_comments', JSON.stringify(comments));
        fd.append('direction', currentDir?.id ?? activeOperator?.direction_id);
        const ad = getAppealDateISO();
        if (ad) fd.append('appeal_date', ad);
        if (existingEvaluation?.isReevaluation) { fd.append('previous_version_id', existingEvaluation.id); fd.append('is_correction', true); }
        if (callFile) fd.append('audio_file', callFile);
        try {
            if (isCalibrationAddCallMode) {
                if (!calibrationRoomId) throw new Error('Не выбрана комната калибровки');
                const payload = new FormData();
                payload.append('operator_id', String(activeOperator?.id || ''));
                payload.append('phone_number', phoneNumber);
                payload.append('score', String(totalScore));
                payload.append('comment', fd.get('comment') || '');
                payload.append('month', assignedMonth);
                payload.append('scores', JSON.stringify(scores));
                payload.append('criterion_comments', JSON.stringify(comments));
                payload.append('direction', String(currentDir?.id ?? activeOperator?.direction_id ?? ''));
                if (ad) payload.append('appeal_date', ad);
                if (callFile) payload.append('audio_file', callFile);
                const r = await authFetch(`${API_BASE_URL}/api/call_calibration/rooms/${calibrationRoomId}/calls`, { method:'POST', headers:{'X-User-Id':userId}, body: payload });
                const res = await r.json();
                if (!r.ok || res.status !== 'success') throw new Error(res.error || 'Не удалось добавить звонок');
                onCalibrationCallCreated?.(res.room_call_id);
                emitCallEvaluationToast('Звонок добавлен в комнату калибровки', 'success');
                onClose();
                return;
            }
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation`, { method:'POST', headers:{'X-User-Id':userId}, body: fd });
            const res = await r.json();
            if (res.status === 'success') {
                onSubmit({
                    id: res.evaluation_id,
                    evaluator: userName,
                    operator: activeOperator?.name || '',
                    phoneNumber,
                    totalScore: totalScore.toFixed(2),
                    comment: fd.get('comment'),
                    commentVisibleToOperator: !!commentVisibleToOperator,
                    selectedDirection: currentDir?.name,
                    directionId: currentDir?.id,
                    is_imported: false,
                    directions: [{name: currentDir?.name, hasFileUpload: currentDir?.hasFileUpload, criteria}],
                    scores,
                    criterionComments: comments,
                    audioUrl: currentDir?.hasFileUpload ? audioUrl : null,
                    isDraft: draft,
                    assignedMonth,
                    isCorrection: existingEvaluation?.isReevaluation || false,
                    appeal_date: ad
                });
                onClose();
            } else emitCallEvaluationToast('Ошибка: ' + res.error, 'error');
        } catch(e) { emitCallEvaluationToast('Ошибка отправки: ' + e.message, 'error'); }
        finally { setIsSubmitting(false); }
    };

    const handleDeleteDraft = async () => {
        if (!existingEvaluation?.isDraft) return;
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation/${existingEvaluation.id}`, { method:'DELETE', headers:{'X-User-Id':userId} });
            const res = await r.json();
            if (res.status === 'success') { onSubmit(null); onClose(); }
            else emitCallEvaluationToast('Ошибка удаления: ' + res.error, 'error');
        } catch(e) { emitCallEvaluationToast('Ошибка: ' + e.message, 'error'); }
    };

    if (!isOpen) return null;
    const title = isCalibrationAddCallMode
        ? 'Новый звонок в калибровке'
        : (existingEvaluation?.isReevaluation ? 'Переоценка' : existingEvaluation?.isDraft ? 'Редактирование черновика' : 'Новая оценка');

    return (
        <div className="modal-backdrop">
            <div className="modal modal-wide" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <div>
                        <h2>{title}</h2>
                        <div className="modal-header-sub">Оператор: {activeOperator?.name || 'Не выбран'}</div>
                    </div>
                    <button className="close-btn" onClick={onClose}><FaIcon className="fas fa-times" /></button>
                </div>
                <div className="modal-body">
                    {isCalibrationAddCallMode && canSelectCalibrationSupervisor && (
                        <div className="field" style={{ marginBottom: 16 }}>
                            <label className="label">Супервайзер</label>
                            <select
                                className="select"
                                value={selectedCalibrationSupervisorId || ''}
                                style={selectedCalibrationSupervisorIsFired ? { color:'var(--text-3)' } : undefined}
                                onChange={(e) => {
                                    const nextId = parseInt(e.target.value, 10) || null;
                                    setSelectedCalibrationSupervisorId(nextId);
                                    setSelectedCalibrationOperatorId(null);
                                }}
                            >
                                <option value="">Выбрать супервайзера</option>
                                {orderedCalibrationSupervisors.map((sv) => (
                                    <option
                                        key={sv.id}
                                        value={sv.id}
                                        className={isFiredStatus(sv?.status) ? 'option-fired' : ''}
                                        style={isFiredStatus(sv?.status) ? { color:'var(--text-3)' } : undefined}
                                    >
                                        {sv.name}
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}
                    {isCalibrationAddCallMode && (
                        <div className="field" style={{ marginBottom: 16 }}>
                            <label className="label">Оператор</label>
                            <select
                                className="select"
                                value={selectedCalibrationOperatorId || ''}
                                disabled={isCalibrationOperatorsLoading}
                                style={selectedCalibrationOperatorIsFired ? { color:'var(--text-3)' } : undefined}
                                onChange={(e) => {
                                    const nextId = parseInt(e.target.value, 10) || null;
                                    setSelectedCalibrationOperatorId(nextId);
                                    const op = calibrationOperatorPool.find(x => x.id === nextId) || null;
                                    const defaultDir = directions.find(d => d.id === op?.direction_id) || directions?.[0] || null;
                                    if (defaultDir) {
                                        setSelectedDirId(defaultDir.id);
                                        setScores(defaultDir.criteria?.map(() => 'Correct') || []);
                                        setComments(defaultDir.criteria?.map(() => '') || []);
                                        setCommentVisible(defaultDir.criteria?.map(() => false) || []);
                                    }
                                }}
                            >
                                <option value="">
                                    {isCalibrationOperatorsLoading ? 'Загрузка операторов...' : 'Выбрать оператора'}
                                </option>
                                {orderedCalibrationOperators.map(op => (
                                    <option
                                        key={op.id}
                                        value={op.id}
                                        className={isFiredStatus(op?.status) ? 'option-fired' : ''}
                                        style={isFiredStatus(op?.status) ? { color:'var(--text-3)' } : undefined}
                                    >
                                        {op.name}
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}
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
                                        <FaIcon className={`fas ${callFile ? 'fa-check-circle' : 'fa-cloud-upload-alt'}`} />
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
                            {durationMismatch && <div className="duration-error"><FaIcon className="fas fa-exclamation-circle" />{audioError}</div>}
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

                    {!isCalibrationAddCallMode && (
                        <>
                            <div className="section-divider">Общий комментарий</div>
                            <div className="field" style={{marginBottom: 10}}>
                                <label className="label">Комментарий (необязательно)</label>
                                <textarea
                                    className="textarea"
                                    value={generalComment}
                                    onChange={e => setGeneralComment(e.target.value)}
                                    placeholder="Общий комментарий по оценке"
                                />
                            </div>
                            <label className="comment-visibility-toggle">
                                <input
                                    type="checkbox"
                                    checked={!!commentVisibleToOperator}
                                    onChange={e => setCommentVisibleToOperator(e.target.checked)}
                                />
                                <span>Показывать оператору</span>
                            </label>
                        </>
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
                            <FaIcon className="fas fa-trash" /> Удалить черновик
                        </button>
                    )}
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary" onClick={() => handleSubmit(false)} disabled={isSubmitDisabled || isSubmitting}>
                        {isSubmitting ? (
                            <><span className="spinner" /> {isCalibrationAddCallMode ? 'Добавление...' : 'Отправка...'}</>
                        ) : (
                            <><FaIcon className="fas fa-check" /> {isCalibrationAddCallMode ? 'Добавить звонок' : 'Отправить'}</>
                        )}
                    </button>
                </div>
            </div>

            {/* Info side panel */}
            {infoIndex !== null && criteria[infoIndex] && (
                <div className="info-panel" onClick={e => e.stopPropagation()}>
                    <div className="info-panel-header">
                        <span className="info-panel-title">{criteria[infoIndex].name}</span>
                        <button className="close-btn" onClick={() => setInfoIndex(null)}><FaIcon className="fas fa-times" /></button>
                    </div>
                    <div className="info-panel-body" dangerouslySetInnerHTML={{__html: parseToHtml(String(criteria[infoIndex].value || 'Описание отсутствует'))}} />
                </div>
            )}
        </div>
    );
};

const CalibrationReviewModal = ({ isOpen, onClose, callEntry, userId, onSubmitted }) => {
    const [scores, setScores] = useState([]);
    const [comments, setComments] = useState([]);
    const [commentVisible, setCommentVisible] = useState([]);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [infoIndex, setInfoIndex] = useState(null);

    const criteria = callEntry?.direction?.criteria || [];

    useEffect(() => {
        if (!isOpen || !callEntry) return;
        const myScores = callEntry?.my_evaluation?.scores;
        const myComments = callEntry?.my_evaluation?.criterion_comments;
        const nextScores = Array.from({ length: criteria.length }, (_, i) => normalizeCalibrationScore(myScores?.[i] ?? 'Correct'));
        const nextComments = Array.from({ length: criteria.length }, (_, i) => String(myComments?.[i] ?? ''));
        setScores(nextScores);
        setComments(nextComments);
        setCommentVisible(nextComments.map(x => !!x?.trim()));
    }, [isOpen, callEntry, criteria.length]);

    const hasCriticalError = criteria.some((c, i) => c?.isCritical && scores[i] === 'Error');
    const totalScore = hasCriticalError ? 0 : criteria.reduce((sum, c, i) => {
        if (c?.isCritical) return sum;
        if (scores[i] === 'Correct' || scores[i] === 'N/A') return sum + (Number(c?.weight) || 0);
        if (scores[i] === 'Deficiency' && c?.deficiency?.weight != null) return sum + (Number(c?.deficiency?.weight) || 0);
        return sum;
    }, 0);
    const isSubmitDisabled = !callEntry?.id || !callEntry?.room_id || !criteria.length || scores.some((s, i) => (s === 'Error' || s === 'Incorrect') && !comments[i]?.trim());

    const submit = async () => {
        if (isSubmitDisabled || !callEntry?.id || !callEntry?.room_id) return;
        setIsSubmitting(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_calibration/rooms/${callEntry.room_id}/evaluate`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Id': userId
                },
                body: JSON.stringify({
                    call_id: callEntry.id,
                    scores,
                    criterion_comments: comments,
                    comment: criteria
                        .map((c, i) => comments[i] ? `${c?.name || `Критерий ${i + 1}`}: ${comments[i]}` : '')
                        .filter(Boolean)
                        .join('; ')
                })
            });
            const data = await r.json();
            if (!r.ok || data.status !== 'success') throw new Error(data.error || 'Не удалось сохранить оценку');
            emitCallEvaluationToast('Оценка калибровки сохранена', 'success');
            onSubmitted?.();
            onClose?.();
        } catch (e) {
            emitCallEvaluationToast('Ошибка: ' + e.message, 'error');
        } finally {
            setIsSubmitting(false);
        }
    };

    if (!isOpen || !callEntry) return null;

    return (
        <div className="modal-backdrop">
            <div className="modal modal-wide" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <div>
                        <h2>Оценка в калибровке</h2>
                        <div className="modal-header-sub">Комната #{callEntry.room_id} · Звонок #{callEntry.id} · Эталон: {Number(callEntry?.score || 0).toFixed(1)}</div>
                    </div>
                    <button className="close-btn" onClick={onClose}><FaIcon className="fas fa-times" /></button>
                </div>
                <div className="modal-body">
                    <div className="section-divider">Данные звонка</div>
                    <div className="grid-2">
                        <div className="field"><label className="label">Оператор</label><input className="input" type="text" value={callEntry?.operator?.name || ''} readOnly /></div>
                        <div className="field"><label className="label">Телефон</label><input className="input" type="text" value={callEntry?.phone_number || ''} readOnly /></div>
                    </div>
                    <div className="field">
                        <label className="label">Дата обращения</label>
                        <input className="input" type="text" value={callEntry?.appeal_date || ''} readOnly />
                    </div>
                    {callEntry?.audio_url && (
                        <div className="audio-wrap" style={{ maxWidth: 520 }}>
                            <div className="audio-label">Аудиозапись</div>
                            <audio controls><source src={callEntry.audio_url} type="audio/mpeg" /></audio>
                        </div>
                    )}

                    <div className="section-divider">Критерии оценивания</div>
                    {!criteria.length ? (
                        <div style={{padding:'12px',color:'var(--red)',fontSize:13}}>У направления нет критериев.</div>
                    ) : (
                        <div style={{maxHeight: 420, overflowY: 'auto', paddingRight: 4}}>
                            {criteria.map((criterion, i) => (
                                <CriterionCard
                                    key={i}
                                    criterion={criterion}
                                    index={i}
                                    score={scores[i] || 'Correct'}
                                    comment={comments[i]}
                                    commentVisible={commentVisible[i]}
                                    onScoreChange={val => { const next=[...scores]; next[i]=val; setScores(next); }}
                                    onCommentChange={val => { const next=[...comments]; next[i]=val; setComments(next); }}
                                    onToggleComment={() => { const next=[...commentVisible]; next[i]=!next[i]; setCommentVisible(next); }}
                                    onShowInfo={() => setInfoIndex(infoIndex===i ? null : i)}
                                />
                            ))}
                        </div>
                    )}

                    <div className="score-summary" style={{marginTop:12, borderRadius:'var(--radius)', border:'1px solid var(--border)'}}>
                        <span style={{fontSize:13, color:'var(--text-2)'}}>Итоговый балл</span>
                        <span className="score-summary-val" style={{color: hasCriticalError ? 'var(--red)' : totalScore >= 70 ? 'var(--green)' : totalScore >= 50 ? 'var(--amber)' : 'var(--red)'}}>
                            {hasCriticalError ? '0' : totalScore} / 100
                        </span>
                    </div>
                </div>
                <div className="modal-footer">
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary" onClick={submit} disabled={isSubmitDisabled || isSubmitting}>
                        {isSubmitting ? <><span className="spinner" /> Сохранение...</> : <><FaIcon className="fas fa-check" /> Отправить оценку</>}
                    </button>
                </div>
            </div>

            {infoIndex !== null && criteria[infoIndex] && (
                <div className="info-panel" onClick={e => e.stopPropagation()}>
                    <div className="info-panel-header">
                        <span className="info-panel-title">{criteria[infoIndex].name}</span>
                        <button className="close-btn" onClick={() => setInfoIndex(null)}><FaIcon className="fas fa-times" /></button>
                    </div>
                    <div className="info-panel-body" dangerouslySetInnerHTML={{__html: parseToHtml(String(criteria[infoIndex].value || 'Описание отсутствует'))}} />
                </div>
            )}
        </div>
    );
};

const CalibrationRoomCreateModal = ({ isOpen, onClose, userId, month, onCreated }) => {
    const [roomTitle, setRoomTitle] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);

    useEffect(() => {
        if (!isOpen) return;
        setRoomTitle('');
        setIsSubmitting(false);
    }, [isOpen, month]);

    const submit = async () => {
        if (!month || isSubmitting) return;
        setIsSubmitting(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_calibration/rooms`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Id': userId
                },
                body: JSON.stringify({
                    month,
                    room_title: String(roomTitle || '').trim()
                })
            });
            const data = await r.json();
            if (!r.ok || data.status !== 'success') throw new Error(data.error || 'Не удалось создать комнату');
            emitCallEvaluationToast('Комната калибровки создана', 'success');
            onCreated?.(data.room_id);
            onClose?.();
        } catch (e) {
            emitCallEvaluationToast('Ошибка: ' + e.message, 'error');
        } finally {
            setIsSubmitting(false);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="modal-backdrop">
            <div className="modal" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <div>
                        <h2>Новая комната калибровки</h2>
                        <div className="modal-header-sub">Месяц: {month}</div>
                    </div>
                    <button className="close-btn" onClick={onClose}><FaIcon className="fas fa-times" /></button>
                </div>
                <div className="modal-body">
                    <div className="field">
                        <label className="label">Название комнаты (необязательно)</label>
                        <input
                            className="input"
                            type="text"
                            value={roomTitle}
                            onChange={e => setRoomTitle(e.target.value)}
                            maxLength={255}
                            placeholder="Например: Апрель - первая калибровка"
                        />
                    </div>
                </div>
                <div className="modal-footer">
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary" onClick={submit} disabled={isSubmitting}>
                        {isSubmitting ? <><span className="spinner" /> Создание...</> : <><FaIcon className="fas fa-check" /> Создать комнату</>}
                    </button>
                </div>
            </div>
        </div>
    );
};

// ─── Main App ──────────────────────────────────────────
const App = ({ user, initialSelection }) => {
    const userId = user?.id;
    const userRole = user?.role;
    const userName = user?.name;
    const normalizedRole = String(userRole || '').trim().toLowerCase();
    const canonicalRole = normalizedRole === 'supervisor' ? 'sv' : normalizedRole;
    const isAdminRole = canonicalRole === 'admin' || canonicalRole === 'super_admin';
    const isSupervisorRole = canonicalRole === 'sv';
    const canUseCalibration = isAdminRole || isSupervisorRole;
    const canManageCalibrationRooms = isAdminRole || isSupervisorRole;
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
    const [showFeedbackModal, setShowFeedbackModal] = useState(false);
    const [feedbackTargetCall, setFeedbackTargetCall] = useState(null);
    const [evalModalMode, setEvalModalMode] = useState('journal');
    const [evaluationTarget, setEvaluationTarget] = useState(null);
    const [isLoading, setIsLoading] = useState(false);
    const [loadingCallId, setLoadingCallId] = useState(null);
    const [operatorFromToken, setOperatorFromToken] = useState(null);
    const [fromDate, setFromDate] = useState(null);
    const [toDate, setToDate] = useState(null);
    const [viewMode, setViewMode] = useState('normal');
    const [activeSection, setActiveSection] = useState('journal');
    const [calibrationRooms, setCalibrationRooms] = useState([]);
    const [isCalibrationLoading, setIsCalibrationLoading] = useState(false);
    const [isCalibrationExporting, setIsCalibrationExporting] = useState(false);
    const [activeCalibrationRoomId, setActiveCalibrationRoomId] = useState(null);
    const [activeCalibrationCallId, setActiveCalibrationCallId] = useState(null);
    const [calibrationDetail, setCalibrationDetail] = useState(null);
    const [showCalibrationEvalModal, setShowCalibrationEvalModal] = useState(false);
    const [showCalibrationCreateModal, setShowCalibrationCreateModal] = useState(false);
    const [isEditingCalibrationRoomTitle, setIsEditingCalibrationRoomTitle] = useState(false);
    const [calibrationRoomTitleDraft, setCalibrationRoomTitleDraft] = useState('');
    const [isSavingCalibrationRoomTitle, setIsSavingCalibrationRoomTitle] = useState(false);
    const [etalonScoresDraft, setEtalonScoresDraft] = useState([]);
    const [etalonCommentsDraft, setEtalonCommentsDraft] = useState([]);
    const [isSavingEtalon, setIsSavingEtalon] = useState(false);
    const [generalCommentDraft, setGeneralCommentDraft] = useState('');
    const [isSavingGeneralComment, setIsSavingGeneralComment] = useState(false);
    const [showCalibrationHistoryModal, setShowCalibrationHistoryModal] = useState(false);
    const [calibrationHistory, setCalibrationHistory] = useState([]);
    const [isCalibrationHistoryLoading, setIsCalibrationHistoryLoading] = useState(false);
    const [openingCalibrationRoomId, setOpeningCalibrationRoomId] = useState(null);
    const [openingCalibrationCallId, setOpeningCalibrationCallId] = useState(null);
    const [showVersionsModal, setShowVersionsModal] = useState(false);
    const [versionHistory, setVersionHistory] = useState([]);
    const operatorsCacheRef = useRef(new Map());
    const callsCacheRef = useRef(new Map());
    const evaluationTargetCacheRef = useRef(new Map());
    const calibrationJoinInFlightRef = useRef(new Map());
    const calibrationDetailInFlightRef = useRef(new Map());
    const calibrationDetailCacheRef = useRef(new Map());
    const DEFAULT_MAX_EVALS = 20;

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
        commentVisibleToOperator: ev.comment_visible_to_operator !== false,
        feedback: ev.feedback || null,
        _rawEvaluation: ev
    }), []);

    // Supervisors
    useEffect(() => {
        if (!(isAdminRole || isSupervisorRole) || !userId) return;
        authFetch(`${API_BASE_URL}/api/admin/sv_list`, { headers:{'X-User-Id':userId} })
            .then(r=>r.json()).then(d=>{ if(d.status==='success') setSupervisors(d.sv_list||[]); }).catch(console.error);
    }, [isAdminRole, isSupervisorRole, userId]);

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
        if (!isSupervisorRole || !userId || !Array.isArray(supervisors) || supervisors.length === 0) return;
        const hasSelected = selectedSupervisor && supervisors.some((sv) => Number(sv.id) === Number(selectedSupervisor));
        if (hasSelected) return;
        const hasSelfInList = supervisors.some((sv) => Number(sv.id) === Number(userId));
        const fallbackSupervisorId = hasSelfInList
            ? Number(userId)
            : (Number(supervisors?.[0]?.id) || null);
        if (fallbackSupervisorId) setSelectedSupervisor(fallbackSupervisorId);
    }, [isSupervisorRole, userId, supervisors, selectedSupervisor]);

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
        const scopeId = (isAdminRole || isSupervisorRole) ? selectedSupervisor : userId;
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
                    const rawOperators = d.operators || [];
                    const scopedSupervisorId = Number(scopeId);
                    const hasSupervisorMeta = rawOperators.some((op) => {
                        const opSupervisorId = Number(op?.supervisor_id ?? op?.sv_id ?? op?.supervisorId);
                        return Number.isFinite(opSupervisorId) && opSupervisorId > 0;
                    });
                    const filteredOperators = rawOperators.filter((op) => {
                        const opSupervisorId = Number(op?.supervisor_id ?? op?.sv_id ?? op?.supervisorId);
                        return Number.isFinite(opSupervisorId) && opSupervisorId === scopedSupervisorId;
                    });
                    const nextOperators = hasSupervisorMeta ? filteredOperators : rawOperators;
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
    }, [userId, isAdminRole, isSupervisorRole, selectedSupervisor, getOperatorsCacheKey]);

    // Evaluations fetch
    const fetchEvaluations = useCallback(async ({ force = false } = {}) => {
        if (!selectedOperator || !userId) { setCalls([]); setEvaluationTarget(null); return; }
        const isOperatorFromLoadedList = operators.some(op => op.id === selectedOperator.id);
        if (!isOperatorFromLoadedList) { setCalls([]); setEvaluationTarget(null); return; }
        const cacheKey = getCallsCacheKey(selectedOperator.id, selectedMonth);
        if (!force && callsCacheRef.current.has(cacheKey)) {
            setCalls(callsCacheRef.current.get(cacheKey) || []);
            setEvaluationTarget(evaluationTargetCacheRef.current.get(cacheKey) || null);
            return;
        }
        setEvaluationTarget(null);
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
                const nextEvaluationTarget = d.evaluation_target || null;
                callsCacheRef.current.set(cacheKey, nextCalls);
                evaluationTargetCacheRef.current.set(cacheKey, nextEvaluationTarget);
                setCalls(nextCalls);
                setEvaluationTarget(nextEvaluationTarget);
            }
        } catch(e) { console.error(e); }
        finally { setIsLoading(false); }
    }, [selectedOperator, userId, selectedMonth, operators, getCallsCacheKey, mapEvaluationToCall]);

    useEffect(() => { fetchEvaluations(); }, [fetchEvaluations]);
    useEffect(() => { setFromDate(null); setToDate(null); }, [selectedMonth]);
    useEffect(() => {
        calibrationDetailCacheRef.current.clear();
        calibrationDetailInFlightRef.current.clear();
        calibrationJoinInFlightRef.current.clear();
    }, [selectedMonth, userId]);

    const fetchCalibrationRooms = useCallback(async () => {
        if (!userId || !canUseCalibration) return;
        setIsCalibrationLoading(true);
        try {
            const params = new URLSearchParams({ month: selectedMonth });
            const r = await authFetch(`${API_BASE_URL}/api/call_calibration/rooms?${params.toString()}`, {
                headers: { 'X-User-Id': userId }
            });
            const d = await r.json();
            if (!r.ok || d.status !== 'success') throw new Error(d.error || 'Не удалось загрузить комнаты');
            const nextRooms = d.rooms || [];
            setCalibrationRooms(nextRooms);
            if (activeCalibrationRoomId && !nextRooms.some(x => x.id === activeCalibrationRoomId)) {
                setActiveCalibrationRoomId(null);
                setActiveCalibrationCallId(null);
                setCalibrationDetail(null);
            }
        } catch (e) {
            emitCallEvaluationToast('Ошибка загрузки калибровки: ' + e.message, 'error');
        } finally {
            setIsCalibrationLoading(false);
        }
    }, [userId, canUseCalibration, selectedMonth, activeCalibrationRoomId]);

    const applyCalibrationDetailPayload = useCallback((roomId, d) => {
        const selectedCallPayload = d?.selected_call
            ? { ...(d.selected_call || {}), my_evaluation: d.my_evaluation || null }
            : null;
        setCalibrationDetail({
            room: d?.room || null,
            calls: d?.calls || [],
            selected_call: selectedCallPayload,
            selected_call_id: d?.selected_call_id || null,
            can_evaluate: !!d?.can_evaluate,
            can_view_results: !!d?.can_view_results,
            joined: !!d?.joined,
            results: d?.results || null,
            evaluators: d?.evaluators || []
        });
        setActiveCalibrationRoomId(roomId);
        setActiveCalibrationCallId(d?.selected_call_id || null);
    }, []);

    const fetchCalibrationRoomDetail = useCallback(async (roomId, callId = null, { force = false } = {}) => {
        if (!userId || !roomId) return null;
        const detailKey = `${roomId}:${callId || ''}`;

        if (!force) {
            const cached = calibrationDetailCacheRef.current.get(detailKey);
            if (cached) {
                applyCalibrationDetailPayload(roomId, cached);
                return cached;
            }
        }

        const inFlight = calibrationDetailInFlightRef.current.get(detailKey);
        if (inFlight) {
            try {
                const data = await inFlight;
                if (data) applyCalibrationDetailPayload(roomId, data);
                return data;
            } catch {
                return null;
            }
        }

        const requestPromise = (async () => {
            const params = new URLSearchParams();
            if (callId) params.append('call_id', String(callId));
            const url = params.toString()
                ? `${API_BASE_URL}/api/call_calibration/rooms/${roomId}?${params.toString()}`
                : `${API_BASE_URL}/api/call_calibration/rooms/${roomId}`;
            const r = await authFetch(url, {
                headers: { 'X-User-Id': userId }
            });
            const d = await r.json();
            if (!r.ok || d.status !== 'success') throw new Error(d.error || 'Не удалось загрузить комнату');
            return d;
        })();

        calibrationDetailInFlightRef.current.set(detailKey, requestPromise);
        try {
            const d = await requestPromise;
            calibrationDetailCacheRef.current.set(detailKey, d);
            if (d?.selected_call_id) {
                calibrationDetailCacheRef.current.set(`${roomId}:${d.selected_call_id}`, d);
            }
            applyCalibrationDetailPayload(roomId, d);
            return d;
        } catch (e) {
            emitCallEvaluationToast('Ошибка загрузки комнаты: ' + e.message, 'error');
            return null;
        } finally {
            calibrationDetailInFlightRef.current.delete(detailKey);
        }
    }, [userId, applyCalibrationDetailPayload]);

    useEffect(() => {
        if (activeSection !== 'calibration') return;
        fetchCalibrationRooms();
    }, [activeSection, fetchCalibrationRooms]);

    useEffect(() => {
        window.__callEvaluationFocus = () => {
            callsCacheRef.current.clear();
            evaluationTargetCacheRef.current.clear();
            fetchEvaluations({ force: true });
            if (activeSection === 'calibration') {
                calibrationDetailCacheRef.current.clear();
                fetchCalibrationRooms();
            }
        };
        return () => { window.__callEvaluationFocus = null; };
    }, [fetchEvaluations, fetchCalibrationRooms, activeSection]);

    const handleOpenCalibrationRoom = useCallback(async (room, callId = null) => {
        if (!room?.id || !userId) return;
        if (openingCalibrationRoomId === room.id) return;
        if (Number(activeCalibrationRoomId) === Number(room.id) && !callId) return;
        try {
            setOpeningCalibrationRoomId(room.id);
            let joinedNow = false;
            if (isSupervisorRole && !room.joined) {
                let joinPromise = calibrationJoinInFlightRef.current.get(room.id);
                if (!joinPromise) {
                    joinPromise = (async () => {
                        const jr = await authFetch(`${API_BASE_URL}/api/call_calibration/rooms/${room.id}/join`, {
                            method: 'POST',
                            headers: { 'X-User-Id': userId }
                        });
                        const jd = await jr.json();
                        if (!jr.ok || jd.status !== 'success') {
                            throw new Error(jd.error || 'Не удалось войти в комнату');
                        }
                        return true;
                    })();
                    calibrationJoinInFlightRef.current.set(room.id, joinPromise);
                }
                await joinPromise;
                calibrationJoinInFlightRef.current.delete(room.id);
                joinedNow = true;
                setCalibrationRooms(prev => prev.map(x => (Number(x.id) === Number(room.id) ? { ...x, joined: true } : x)));
            }
            await fetchCalibrationRoomDetail(room.id, callId);
            if (joinedNow) fetchCalibrationRooms();
        } catch (e) {
            emitCallEvaluationToast('Ошибка: ' + e.message, 'error');
            calibrationJoinInFlightRef.current.delete(room.id);
        } finally {
            setOpeningCalibrationRoomId((prev) => (Number(prev) === Number(room.id) ? null : prev));
        }
    }, [userId, isSupervisorRole, fetchCalibrationRoomDetail, fetchCalibrationRooms, activeCalibrationRoomId, openingCalibrationRoomId]);

    const handleCalibrationRoomCreated = useCallback(async (roomId) => {
        setActiveSection('calibration');
        calibrationDetailCacheRef.current.clear();
        await fetchCalibrationRooms();
        if (roomId) {
            await fetchCalibrationRoomDetail(roomId);
            if (operators.length) {
                setEditingEval(null);
                setShowEvalModal(true);
                setEvalModalMode('calibration_add_call');
            } else {
                emitCallEvaluationToast('Комната создана. Загрузите операторов и добавьте звонок.', 'info');
            }
        }
    }, [fetchCalibrationRooms, fetchCalibrationRoomDetail, operators.length]);

    const handleCalibrationCallCreated = useCallback(async (roomCallId) => {
        if (!activeCalibrationRoomId) return;
        calibrationDetailCacheRef.current.clear();
        await fetchCalibrationRoomDetail(activeCalibrationRoomId, roomCallId || null, { force: true });
        await fetchCalibrationRooms();
    }, [activeCalibrationRoomId, fetchCalibrationRoomDetail, fetchCalibrationRooms]);

    const handleCalibrationEvaluationSaved = useCallback(async () => {
        if (!activeCalibrationRoomId) return;
        calibrationDetailCacheRef.current.clear();
        await fetchCalibrationRoomDetail(activeCalibrationRoomId, activeCalibrationCallId || null, { force: true });
        await fetchCalibrationRooms();
    }, [activeCalibrationRoomId, activeCalibrationCallId, fetchCalibrationRoomDetail, fetchCalibrationRooms]);

    const handleOpenCalibrationCall = useCallback(async (callId) => {
        if (!activeCalibrationRoomId || !callId) return;
        if (Number(callId) === Number(activeCalibrationCallId)) return;
        if (Number(openingCalibrationCallId) === Number(callId)) return;
        try {
            setOpeningCalibrationCallId(callId);
            await fetchCalibrationRoomDetail(activeCalibrationRoomId, callId);
        } finally {
            setOpeningCalibrationCallId((prev) => (Number(prev) === Number(callId) ? null : prev));
        }
    }, [activeCalibrationRoomId, activeCalibrationCallId, openingCalibrationCallId, fetchCalibrationRoomDetail]);

    const handleExportCalibrationRoom = useCallback(async () => {
        if (!activeCalibrationRoomId || !userId || isCalibrationExporting) return;
        setIsCalibrationExporting(true);
        try {
            const r = await authFetch(
                `${API_BASE_URL}/api/call_calibration/rooms/${activeCalibrationRoomId}/export_excel`,
                { headers: { 'X-User-Id': userId } }
            );
            if (!r.ok) {
                const d = await readJsonSafe(r);
                throw new Error(d?.error || 'Не удалось выгрузить результаты калибровки');
            }

            const blob = await r.blob();
            const contentDisposition = r.headers.get('content-disposition') || '';
            let filename = `calibration_room_${activeCalibrationRoomId}.xlsx`;
            const utf8NameMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
            const plainNameMatch = contentDisposition.match(/filename=\"?([^\";]+)\"?/i);
            if (utf8NameMatch?.[1]) {
                try {
                    filename = decodeURIComponent(utf8NameMatch[1]);
                } catch {
                    filename = utf8NameMatch[1];
                }
            } else if (plainNameMatch?.[1]) {
                filename = plainNameMatch[1];
            }

            const objectUrl = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = objectUrl;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            window.URL.revokeObjectURL(objectUrl);

            emitCallEvaluationToast('Результаты калибровки выгружены в Excel', 'success');
        } catch (e) {
            emitCallEvaluationToast('Ошибка выгрузки: ' + e.message, 'error');
        } finally {
            setIsCalibrationExporting(false);
        }
    }, [activeCalibrationRoomId, userId, isCalibrationExporting]);

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
                commentVisibleToOperator: data.commentVisibleToOperator !== false,
                feedback: null,
                sv_request: false, sv_request_approved: false,
                _rawEvaluation: {
                    comment_visible_to_operator: data.commentVisibleToOperator !== false
                }
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

    const handleFeedbackSaved = useCallback(async () => {
        setShowFeedbackModal(false);
        setFeedbackTargetCall(null);
        await fetchEvaluations({ force: true });
    }, [fetchEvaluations]);

    const handleSelectCall = async (callId) => {
        const call = calls.find(c => c.id === callId);
        if (!call) return;
        if (call.isDraft) { setEvalModalMode('journal'); setEditingEval(call); setShowEvalModal(true); return; }
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
        } catch(e) { emitCallEvaluationToast('Ошибка: ' + e.message, 'error'); }
    };

    const handleDeleteCalibrationRoom = async (e, roomId) => {
        e.stopPropagation();
        if (!confirm('Удалить комнату калибровки? Все данные комнаты будут удалены.')) return;
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_calibration/rooms/${roomId}`, {
                method: 'DELETE',
                headers: { 'X-User-Id': userId }
            });
            const data = await r.json();
            if (!r.ok) throw new Error(data.error || 'Ошибка удаления');
            setCalibrationRooms(prev => prev.filter(room => room.id !== roomId));
            if (activeCalibrationRoomId === roomId) {
                setActiveCalibrationRoomId(null);
                setCalibrationDetail(null);
            }
            emitCallEvaluationToast('Комната удалена', 'success');
        } catch (e) {
            emitCallEvaluationToast('Ошибка: ' + e.message, 'error');
        }
    };

    const handleDeleteCalibrationCall = async (e, callId) => {
        e.stopPropagation();
        if (!confirm('Удалить звонок из комнаты калибровки? Все оценки по этому звонку будут удалены.')) return;
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_calibration/rooms/${activeCalibrationRoomId}/calls/${callId}`, {
                method: 'DELETE',
                headers: { 'X-User-Id': userId }
            });
            const data = await r.json();
            if (!r.ok) throw new Error(data.error || 'Ошибка удаления');
            calibrationDetailCacheRef.current.clear();
            setCalibrationDetail(prev => {
                if (!prev) return prev;
                const nextCalls = (prev.calls || []).filter(c => c.id !== callId);
                const nextSelectedCall = prev.selected_call_id === callId ? null : prev.selected_call;
                const nextSelectedCallId = prev.selected_call_id === callId ? null : prev.selected_call_id;
                return { ...prev, calls: nextCalls, selected_call: nextSelectedCall, selected_call_id: nextSelectedCallId };
            });
            if (activeCalibrationCallId === callId) setActiveCalibrationCallId(null);
            await fetchCalibrationRooms();
            emitCallEvaluationToast('Звонок удалён', 'success');
        } catch (e) {
            emitCallEvaluationToast('Ошибка: ' + e.message, 'error');
        }
    };

    const handleSaveCalibrationRoomTitle = useCallback(async () => {
        if (!canManageCalibrationRooms || !activeCalibrationRoomId || isSavingCalibrationRoomTitle) return;
        const nextTitle = String(calibrationRoomTitleDraft || '').trim();
        const currentTitle = String(calibrationDetail?.room?.room_title || '').trim();
        const isDirty = nextTitle !== currentTitle;

        if (nextTitle.length > 255) {
            emitCallEvaluationToast('Название комнаты не должно превышать 255 символов', 'error');
            return;
        }
        if (!isDirty) {
            setIsEditingCalibrationRoomTitle(false);
            return;
        }

        setIsSavingCalibrationRoomTitle(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_calibration/rooms/${activeCalibrationRoomId}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Id': userId
                },
                body: JSON.stringify({
                    room_title: nextTitle
                })
            });
            const d = await r.json();
            if (!r.ok || d.status !== 'success') throw new Error(d.error || 'Не удалось обновить название комнаты');

            const updatedTitle = String(d?.room?.room_title || `Комната #${activeCalibrationRoomId}`);
            setCalibrationRooms(prev => prev.map(room => (
                Number(room.id) === Number(activeCalibrationRoomId)
                    ? { ...room, room_title: updatedTitle }
                    : room
            )));
            setCalibrationDetail(prev => {
                if (!prev?.room || Number(prev.room.id) !== Number(activeCalibrationRoomId)) return prev;
                return { ...prev, room: { ...prev.room, room_title: updatedTitle } };
            });
            calibrationDetailCacheRef.current.clear();
            setCalibrationRoomTitleDraft(updatedTitle);
            setIsEditingCalibrationRoomTitle(false);
            emitCallEvaluationToast('Название комнаты обновлено', 'success');
        } catch (e) {
            emitCallEvaluationToast('Ошибка: ' + e.message, 'error');
        } finally {
            setIsSavingCalibrationRoomTitle(false);
        }
    }, [
        canManageCalibrationRooms,
        activeCalibrationRoomId,
        isSavingCalibrationRoomTitle,
        calibrationRoomTitleDraft,
        calibrationDetail,
        userId
    ]);

    const handleOpenCalibrationHistory = async () => {
        if (!activeCalibrationRoomId) return;
        setShowCalibrationHistoryModal(true);
        setIsCalibrationHistoryLoading(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_calibration/rooms/${activeCalibrationRoomId}/history`, {
                headers: { 'X-User-Id': userId }
            });
            const data = await r.json();
            if (!r.ok) throw new Error(data.error || 'Ошибка загрузки истории');
            setCalibrationHistory(data.events || []);
        } catch (e) {
            emitCallEvaluationToast('Ошибка: ' + e.message, 'error');
            setShowCalibrationHistoryModal(false);
        } finally {
            setIsCalibrationHistoryLoading(false);
        }
    };

    const callsByMonth = calls.filter(c => c.assignedMonth === selectedMonth);
    let displayedCalls = callsByMonth;
    if (viewMode === 'normal') displayedCalls = displayedCalls.filter(c => (!fromDate||c.date>=fromDate) && (!toDate||c.date<=toDate));
    else displayedCalls = displayedCalls.filter(c => c.date.slice(0,7) !== selectedMonth);

    const hasExtra = callsByMonth.filter(c => c.date.slice(0,7) !== selectedMonth).length > 0;
    const evalCount = displayedCalls.filter(c => !c.isDraft && !c.is_imported).length;
    const avgScore = evalCount > 0 ? displayedCalls.filter(c=>!c.isDraft&&!c.is_imported).reduce((s,c)=>s+parseFloat(c.totalScore),0)/evalCount : 0;
    const targetEvalCount = (() => {
        const parsed = Number(evaluationTarget?.required_calls);
        if (Number.isFinite(parsed) && parsed >= 0) return parsed;
        return DEFAULT_MAX_EVALS;
    })();
    const totalEvaluatedInMonth = callsByMonth.filter(c=>!c.isDraft&&!c.is_imported).length;
    const progressPercent = targetEvalCount > 0
        ? Math.min((totalEvaluatedInMonth / targetEvalCount) * 100, 100)
        : 100;
    const orderedSupervisors = sortByFiredAndName(supervisors);
    const orderedOperators = sortByFiredAndName(operators);
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
    const calibrationRoom = calibrationDetail?.room || null;
    const calibrationCalls = calibrationDetail?.calls || [];
    const calibrationCall = calibrationDetail?.selected_call || null;
    const calibrationResults = calibrationDetail?.results || null;
    const calibrationRows = calibrationResults?.criteria_rows || [];
    const calibrationEvaluators = calibrationDetail?.evaluators || [];
    const calibrationCriteria = calibrationCall?.direction?.criteria || [];
    const calibrationRoomId = calibrationRoom?.id || null;
    const calibrationRoomTitleCurrent = String(calibrationRoom?.room_title || '').trim();
    const calibrationRoomTitleDisplay = calibrationRoom?.room_title || (calibrationRoomId ? `Комната #${calibrationRoomId}` : '');
    const calibrationRoomTitleDraftNormalized = String(calibrationRoomTitleDraft || '').trim();
    const isCalibrationRoomTitleDirty = !!calibrationRoom && calibrationRoomTitleDraftNormalized !== calibrationRoomTitleCurrent;

    useEffect(() => {
        if (!calibrationRoom) {
            setIsEditingCalibrationRoomTitle(false);
            setCalibrationRoomTitleDraft('');
            setIsSavingCalibrationRoomTitle(false);
            return;
        }
        setCalibrationRoomTitleDraft(calibrationRoomTitleDisplay);
        setIsEditingCalibrationRoomTitle(false);
        setIsSavingCalibrationRoomTitle(false);
    }, [calibrationRoomId, calibrationRoomTitleCurrent, calibrationRoomTitleDisplay]);

    useEffect(() => {
        if (!calibrationCall) {
            setEtalonScoresDraft([]);
            setEtalonCommentsDraft([]);
            setGeneralCommentDraft('');
            return;
        }
        const sourceScores = Array.isArray(calibrationCall?.etalon_scores) && calibrationCall.etalon_scores.length
            ? calibrationCall.etalon_scores
            : (Array.isArray(calibrationCall?.scores) ? calibrationCall.scores : []);
        const sourceComments = Array.isArray(calibrationCall?.etalon_criterion_comments) && calibrationCall.etalon_criterion_comments.length
            ? calibrationCall.etalon_criterion_comments
            : (Array.isArray(calibrationCall?.criterion_comments) ? calibrationCall.criterion_comments : []);
        setEtalonScoresDraft(
            Array.from({ length: calibrationCriteria.length }, (_, i) => normalizeCalibrationScore(sourceScores[i] ?? 'Correct'))
        );
        setEtalonCommentsDraft(
            Array.from({ length: calibrationCriteria.length }, (_, i) => String(sourceComments[i] ?? ''))
        );
        setGeneralCommentDraft(calibrationCall?.general_comment ?? '');
    }, [calibrationCall, calibrationCriteria.length]);

    const isEtalonDirty = !!(canManageCalibrationRooms && calibrationCall && calibrationCriteria.length) && calibrationCriteria.some((_, idx) => {
        const srcScore = normalizeCalibrationScore(
            calibrationCall?.etalon_scores?.[idx] ?? calibrationCall?.scores?.[idx] ?? 'Correct'
        );
        const srcComment = String(
            calibrationCall?.etalon_criterion_comments?.[idx] ?? calibrationCall?.criterion_comments?.[idx] ?? ''
        );
        const draftScore = normalizeCalibrationScore(etalonScoresDraft[idx] ?? 'Correct');
        const draftComment = String(etalonCommentsDraft[idx] ?? '');
        return draftScore !== srcScore || draftComment !== srcComment;
    });
    const hasEtalonValidationError = !!(canManageCalibrationRooms && calibrationCriteria.length) && calibrationCriteria.some((criterion, idx) => {
        const score = normalizeCalibrationScore(etalonScoresDraft[idx] ?? 'Correct');
        const comment = String(etalonCommentsDraft[idx] ?? '').trim();
        if (score === 'Error' || score === 'Incorrect') return !comment;
        // In regular evaluation deficiency is treated as negative and usually commented.
        if (score === 'Deficiency' && criterion?.deficiency) return !comment;
        return false;
    });

    const handleSaveEtalon = useCallback(async () => {
        if (!canManageCalibrationRooms || !calibrationCall?.id || !activeCalibrationRoomId) return;
        if (hasEtalonValidationError) {
            emitCallEvaluationToast('Заполните комментарии по ошибкам/недочетам в эталоне', 'error');
            return;
        }
        setIsSavingEtalon(true);
        try {
            const r = await authFetch(
                `${API_BASE_URL}/api/call_calibration/rooms/${activeCalibrationRoomId}/calls/${calibrationCall.id}/etalon`,
                {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-User-Id': userId
                    },
                    body: JSON.stringify({
                        scores: etalonScoresDraft,
                        criterion_comments: etalonCommentsDraft
                    })
                }
            );
            const d = await r.json();
            if (!r.ok || d.status !== 'success') throw new Error(d.error || 'Не удалось сохранить эталон');
            emitCallEvaluationToast('Эталон обновлен', 'success');
            calibrationDetailCacheRef.current.clear();
            await fetchCalibrationRoomDetail(activeCalibrationRoomId, calibrationCall.id, { force: true });
            await fetchCalibrationRooms();
        } catch (e) {
            emitCallEvaluationToast('Ошибка сохранения эталона: ' + e.message, 'error');
        } finally {
            setIsSavingEtalon(false);
        }
    }, [
        canManageCalibrationRooms,
        calibrationCall,
        activeCalibrationRoomId,
        userId,
        etalonScoresDraft,
        etalonCommentsDraft,
        hasEtalonValidationError,
        fetchCalibrationRoomDetail,
        fetchCalibrationRooms
    ]);

    const isGeneralCommentDirty = canManageCalibrationRooms && calibrationCall &&
        (generalCommentDraft ?? '') !== (calibrationCall?.general_comment ?? '');

    const handleSaveGeneralComment = useCallback(async () => {
        if (!calibrationCall?.id || !activeCalibrationRoomId) return;
        setIsSavingGeneralComment(true);
        try {
            const r = await authFetch(
                `${API_BASE_URL}/api/call_calibration/rooms/${activeCalibrationRoomId}/calls/${calibrationCall.id}/general_comment`,
                {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'X-User-Id': userId },
                    body: JSON.stringify({ general_comment: generalCommentDraft.trim() })
                }
            );
            const d = await r.json();
            if (!r.ok || d.status !== 'success') throw new Error(d.error || 'Не удалось сохранить комментарий');
            emitCallEvaluationToast('Общий комментарий сохранён', 'success');
            calibrationDetailCacheRef.current.clear();
            await fetchCalibrationRoomDetail(activeCalibrationRoomId, calibrationCall.id, { force: true });
        } catch (e) {
            emitCallEvaluationToast('Ошибка сохранения: ' + e.message, 'error');
        } finally {
            setIsSavingGeneralComment(false);
        }
    }, [calibrationCall, activeCalibrationRoomId, userId, generalCommentDraft, fetchCalibrationRoomDetail]);

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
                    <div className="panel-title-wrap">
                        <span className="panel-title">{activeSection === 'journal' ? 'Журнал оценок' : 'Калибровка звонков'}</span>
                        {canUseCalibration && (
                            <div className="section-switch">
                                <button
                                    className={`btn btn-sm ${activeSection === 'journal' ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => setActiveSection('journal')}
                                >
                                    Журнал
                                </button>
                                <button
                                    className={`btn btn-sm ${activeSection === 'calibration' ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => setActiveSection('calibration')}
                                >
                                    Калибровка
                                </button>
                            </div>
                        )}
                    </div>
                    <div className="filters">
                        {(isAdminRole || isSupervisorRole) && (
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
                        {activeSection === 'journal' && (
                            <div className="filter-group">
                                <label className="label">Оператор</label>
                                <select className="select" value={selectedOperator?.id||''} style={selectedOperatorIsFired ? { color:'var(--text-3)' } : undefined} onChange={e => { const op=operators.find(o=>o.id===parseInt(e.target.value))||null; setSelectedOperator(op); setCalls([]); setExpandedId(null); }}>
                                    <option value="">Выбрать</option>
                                    {orderedOperators.map(op => (
                                        <option key={op.id} value={op.id} className={isFiredStatus(op?.status) ? 'option-fired' : ''} style={isFiredStatus(op?.status) ? { color:'var(--text-3)' } : undefined}>{op.name}</option>
                                    ))}
                                </select>
                            </div>
                        )}
                        <div className="filter-group">
                            <label className="label">Месяц</label>
                            <select className="select" value={selectedMonth} onChange={e => setSelectedMonth(e.target.value)}>
                                {months.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                            </select>
                        </div>
                        {isAdminRole && activeSection === 'journal' && (() => {
                            const lastDay = new Date(parseInt(selectedMonth.slice(0,4)), parseInt(selectedMonth.slice(5,7)), 0).getDate();
                            return <DateRangePicker minDate={`${selectedMonth}-01`} maxDate={`${selectedMonth}-${String(lastDay).padStart(2,'0')}`} setFromDate={setFromDate} setToDate={setToDate} />;
                        })()}
                    </div>
                </div>

                {activeSection === 'journal' ? (
                    <>
                {/* Stats bar */}
                {selectedOperator && (
                    <div className="stats-bar">
                        <div className="stat-item">
                            <div className="stat-icon blue"><FaIcon className="fas fa-headset" /></div>
                            <div>
                                <div className="stat-value">{totalEvaluatedInMonth} <span style={{fontSize:12,color:'var(--text-2)',fontFamily:'var(--font)',fontWeight:400}}>/ {targetEvalCount}</span></div>
                                <div className="stat-label">Прослушано / нужно</div>
                            </div>
                        </div>
                        <div className="stat-item">
                            <div className="stat-icon green"><FaIcon className="fas fa-chart-line" /></div>
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
                                    <span style={{fontFamily:'var(--font-mono)'}}>{totalEvaluatedInMonth}/{targetEvalCount}</span>
                                </div>
                                <div style={{background:'var(--surface-2)',borderRadius:4,height:6,overflow:'hidden'}}>
                                    <div style={{height:'100%', borderRadius:4, background: targetEvalCount > 0 && totalEvaluatedInMonth/targetEvalCount > 0.8 ? 'var(--green)' : 'var(--accent)', width:`${progressPercent}%`, transition:'width 0.4s ease'}} />
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
                            <div className="empty-state-icon"><FaIcon className="fas fa-inbox" /></div>
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
                                                                <button className="btn btn-green btn-sm" onClick={() => { setEvalModalMode('journal'); setEditingEval(call); setShowEvalModal(true); }}><FaIcon className="fas fa-star" /> Оценить</button>
                                                                {isAdminRole && <button className="btn btn-danger btn-sm" onClick={() => deleteImportedCall(call.id)}><FaIcon className="fas fa-trash" /></button>}
                                                            </>
                                                        ) : (
                                                            <>
                                                                {(isAdminRole || isSupervisorRole) && !call.isDraft && (
                                                                    <button
                                                                        className={`btn btn-sm ${call.feedback ? 'btn-secondary' : 'btn-primary'}`}
                                                                        onClick={() => {
                                                                            setFeedbackTargetCall(call);
                                                                            setShowFeedbackModal(true);
                                                                        }}
                                                                    >
                                                                        <FaIcon className={`fas fa-${call.feedback ? 'pen' : 'comments'}`} />
                                                                        {call.feedback ? 'Ред. ОС' : 'ОС'}
                                                                    </button>
                                                                )}
                                                                <SvRequestButton call={call} userId={userId} userRole={isSupervisorRole ? 'sv' : userRole} fetchEvaluations={fetchEvaluations} onReevaluate={() => { setEvalModalMode('journal'); setEditingEval({...call,isReevaluation:true}); setShowEvalModal(true); }} />
                                                                {isAdminRole && !call.isDraft && (
                                                                    <>
                                                                        <button className="btn btn-secondary btn-sm" onClick={() => { setEvalModalMode('journal'); setEditingEval({...call,isReevaluation:true}); setShowEvalModal(true); }}>
                                                                            <FaIcon className="fas fa-redo" /> Переоценить
                                                                        </button>
                                                                        {call.isCorrection && (
                                                                            <button className="btn btn-secondary btn-sm" onClick={async () => {
                                                                                try {
                                                                                    const r = await authFetch(`${API_BASE_URL}/api/call_versions/${call.id}`, {headers:{'X-User-Id':userId}});
                                                                                    const d = await r.json();
                                                                                    if (d.status==='success') { setVersionHistory(d.versions); setShowVersionsModal(true); }
                                                                                } catch(e) { console.error(e); }
                                                                            }}>
                                                                                <FaIcon className="fas fa-history" />
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
                                                            <div className="expanded-meta-item"><strong>Показ оператору:</strong> {call.commentVisibleToOperator !== false ? 'Да' : 'Нет'}</div>
                                                        </div>
                                                        <div style={{marginBottom:12, fontSize:13, color:'var(--text-2)'}}>
                                                            <strong style={{color:'var(--text)'}}>Общий комментарий:</strong> {call.combinedComment?.trim() || '—'}
                                                        </div>
                                                        {call.feedback && (
                                                            <div style={{marginBottom:12, fontSize:13, color:'var(--text-2)', padding:'10px 12px', border:'1px solid var(--border)', borderRadius:'var(--radius)', background:'var(--surface-2)'}}>
                                                                <div style={{marginBottom:4}}>
                                                                    <strong style={{color:'var(--text)'}}>Обратная связь:</strong> {call.feedback.feedback_comment || '—'}
                                                                </div>
                                                                <div style={{marginBottom:4}}>
                                                                    <strong style={{color:'var(--text)'}}>Как проведена:</strong> {call.feedback.delivery_comment || '—'}
                                                                </div>
                                                                <div>
                                                                    <strong style={{color:'var(--text)'}}>Время:</strong> {call.feedback.date || '—'} {call.feedback.start_time || '—'}–{call.feedback.end_time || '—'}
                                                                </div>
                                                            </div>
                                                        )}
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
                        {isAdminRole && (viewMode==='extra'||hasExtra) && (
                            <button className="btn btn-secondary btn-sm" onClick={() => setViewMode(v=>v==='normal'?'extra':'normal')}>
                                <FaIcon className={`fas fa-${viewMode==='normal'?'filter':'list'}`} /> {viewMode==='normal' ? 'Доп. оценки' : 'Основные'}
                            </button>
                        )}
                        {viewMode === 'normal' && (
                            <button
                                className={`btn btn-primary btn-sm ${!selectedOperator ? 'disabled' : ''}`}
                                style={{opacity:!selectedOperator?0.4:1,cursor:!selectedOperator?'not-allowed':'pointer'}}
                                onClick={() => { if (!selectedOperator) return; setEvalModalMode('journal'); setEditingEval(null); setShowEvalModal(true); }}
                                disabled={!selectedOperator}
                            >
                                <FaIcon className="fas fa-plus" /> Добавить оценку
                            </button>
                        )}
                    </div>
                </div>
                    </>
                ) : (
                    <>
                        <div className="table-wrap">
                            {isCalibrationLoading ? (
                                <div className="calibration-cards">
                                    {[...Array(4)].map((_, idx) => (
                                        <div key={idx} className="calibration-card">
                                            <div className="skeleton" style={{height:16,width:'45%',marginBottom:10}} />
                                            <div className="skeleton" style={{height:14,width:'80%',marginBottom:6}} />
                                            <div className="skeleton" style={{height:14,width:'70%',marginBottom:6}} />
                                            <div className="skeleton" style={{height:14,width:'50%'}} />
                                        </div>
                                    ))}
                                </div>
                            ) : calibrationRooms.length === 0 ? (
                                <div className="empty-state">
                                    <div className="empty-state-icon"><FaIcon className="fas fa-door-open" /></div>
                                    <h3>Нет комнат калибровки</h3>
                                    <p>{canManageCalibrationRooms ? 'Создайте первую комнату калибровки для выбранного месяца.' : 'Пока нет доступных комнат на выбранный месяц.'}</p>
                                </div>
                            ) : (
                                <div className="calibration-cards">
                                    {calibrationRooms.map((room) => {
                                        const isActive = room.id === activeCalibrationRoomId;
                                        const isOpening = Number(openingCalibrationRoomId) === Number(room.id);
                                        return (
                                            <button
                                                key={room.id}
                                                className={`calibration-card ${isActive ? 'active' : ''}`}
                                                onClick={() => handleOpenCalibrationRoom(room)}
                                                disabled={isOpening}
                                            >
                                                <div className="calibration-card-head">
                                                    <span className="version-badge">Комната #{room.id}</span>
                                                    <span className={`badge ${isOpening ? 'badge-blue' : room.my_evaluated ? 'badge-green' : room.joined ? 'badge-amber' : 'badge-blue'}`}>
                                                        <span className="badge-dot" />
                                                        {isOpening ? 'Загрузка...' : room.my_evaluated ? 'Оценено' : room.joined ? 'В комнате' : 'Не вошел'}
                                                    </span>
                                                    {isAdminRole && (
                                                        <button
                                                            className="calibration-card-delete"
                                                            onClick={(e) => handleDeleteCalibrationRoom(e, room.id)}
                                                            title="Удалить комнату"
                                                        ><FaIcon className="fas fa-trash" /></button>
                                                    )}
                                                </div>
                                                <div className="calibration-card-title">{room.room_title || `Комната #${room.id}`}</div>
                                                <div className="calibration-card-meta">
                                                    <span>Звонков: {Number(room.calls_count || 0)}</span>
                                                    <span>Оценили супервайзеры: {Number(room.evaluated_count || 0)}</span>
                                                    <span>Месяц: {room.month || selectedMonth}</span>
                                                </div>
                                            </button>
                                        );
                                    })}
                                </div>
                            )}

                            {calibrationRoom && (
                                <div className="calibration-detail">
                                    <div className="expanded-content" style={{borderTop:'none',paddingTop:18}}>
                                        <div className="calibration-detail-head">
                                            <div>
                                                {canManageCalibrationRooms ? (
                                                    <div style={{display:'flex', alignItems:'center', gap:8, flexWrap:'wrap', marginBottom:6}}>
                                                        {isEditingCalibrationRoomTitle ? (
                                                            <>
                                                                <input
                                                                    className="input"
                                                                    type="text"
                                                                    value={calibrationRoomTitleDraft}
                                                                    onChange={e => setCalibrationRoomTitleDraft(e.target.value)}
                                                                    maxLength={255}
                                                                    placeholder={`Комната #${calibrationRoom.id}`}
                                                                    style={{minWidth:280, maxWidth:'100%', flex:'1 1 280px'}}
                                                                />
                                                                <button
                                                                    className="btn btn-primary btn-sm"
                                                                    onClick={handleSaveCalibrationRoomTitle}
                                                                    disabled={isSavingCalibrationRoomTitle || !isCalibrationRoomTitleDirty}
                                                                >
                                                                    {isSavingCalibrationRoomTitle ? <><span className="spinner" /> Сохранение...</> : <><FaIcon className="fas fa-save" /> Сохранить</>}
                                                                </button>
                                                                <button
                                                                    className="btn btn-secondary btn-sm"
                                                                    onClick={() => {
                                                                        setCalibrationRoomTitleDraft(calibrationRoomTitleDisplay);
                                                                        setIsEditingCalibrationRoomTitle(false);
                                                                    }}
                                                                    disabled={isSavingCalibrationRoomTitle}
                                                                >
                                                                    Отмена
                                                                </button>
                                                            </>
                                                        ) : (
                                                            <>
                                                                <h4 style={{margin:0}}>{calibrationRoomTitleDisplay}</h4>
                                                                <button
                                                                    className="btn btn-secondary btn-sm"
                                                                    onClick={() => {
                                                                        setCalibrationRoomTitleDraft(calibrationRoomTitleDisplay);
                                                                        setIsEditingCalibrationRoomTitle(true);
                                                                    }}
                                                                >
                                                                    <FaIcon className="fas fa-pen" /> Переименовать
                                                                </button>
                                                            </>
                                                        )}
                                                    </div>
                                                ) : (
                                                    <h4>{calibrationRoomTitleDisplay}</h4>
                                                )}
                                                <div className="expanded-meta">
                                                    <div className="expanded-meta-item"><strong>Комната:</strong> #{calibrationRoom.id}</div>
                                                    <div className="expanded-meta-item"><strong>Месяц:</strong> {calibrationRoom.month || selectedMonth}</div>
                                                    <div className="expanded-meta-item"><strong>Создал:</strong> {calibrationRoom.benchmark_admin?.name || '—'}</div>
                                                    <div className="expanded-meta-item"><strong>Звонков:</strong> {calibrationCalls.length}</div>
                                                </div>
                                            </div>
                                            <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={handleExportCalibrationRoom}
                                                    disabled={isCalibrationExporting}
                                                >
                                                    {isCalibrationExporting ? <><span className="spinner" /> Выгрузка...</> : <><FaIcon className="fas fa-file-excel" /> Выгрузить Excel</>}
                                                </button>
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={handleOpenCalibrationHistory}
                                                >
                                                    <FaIcon className="fas fa-history" /> История
                                                </button>
                                                {canManageCalibrationRooms && (
                                                    <button
                                                        className="btn btn-primary btn-sm"
                                                        onClick={() => {
                                                            if (isAdminRole && !supervisors.length) {
                                                                emitCallEvaluationToast('Нет доступных супервайзеров для добавления звонка', 'error');
                                                                return;
                                                            }
                                                            if (isSupervisorRole && !supervisors.length && !operators.length) {
                                                                emitCallEvaluationToast('Нет доступных операторов для добавления звонка', 'error');
                                                                return;
                                                            }
                                                            setEditingEval(null);
                                                            setEvalModalMode('calibration_add_call');
                                                            setShowEvalModal(true);
                                                        }}
                                                    >
                                                        <FaIcon className="fas fa-plus" /> Добавить звонок
                                                    </button>
                                                )}
                                                {isSupervisorRole && calibrationDetail?.can_evaluate && calibrationCall && (
                                                    <button className="btn btn-primary btn-sm" onClick={() => setShowCalibrationEvalModal(true)}>
                                                        <FaIcon className="fas fa-star" /> Оценить звонок
                                                    </button>
                                                )}
                                            </div>
                                        </div>
                                        {calibrationCalls.length === 0 ? (
                                            <div className="calibration-lock-note">В этой комнате пока нет звонков. Добавьте звонок для калибровки.</div>
                                        ) : (
                                            <>
                                                <div className="section-divider">Звонки в комнате</div>
                                                <div className="calibration-cards" style={{padding:'0 0 8px'}}>
                                                    {calibrationCalls.map((call) => {
                                                        const isActiveCall = Number(call.id) === Number(activeCalibrationCallId);
                                                        const isOpeningCall = Number(openingCalibrationCallId) === Number(call.id);
                                                        return (
                                                            <button
                                                                key={call.id}
                                                                className={`calibration-card ${isActiveCall ? 'active' : ''}`}
                                                                onClick={() => handleOpenCalibrationCall(call.id)}
                                                                disabled={isOpeningCall}
                                                            >
                                                                <div className="calibration-card-head">
                                                                    <span className="version-badge">Звонок #{call.id}</span>
                                                                    {isOpeningCall
                                                                        ? <span className="badge badge-blue"><span className="badge-dot" /> Загрузка...</span>
                                                                        : call.my_evaluated
                                                                            ? <span className="badge badge-green"><span className="badge-dot" /> Оценен вами</span>
                                                                            : null}
                                                                    {isAdminRole && (
                                                                        <button
                                                                            className="calibration-card-delete"
                                                                            onClick={(e) => handleDeleteCalibrationCall(e, call.id)}
                                                                            title="Удалить звонок"
                                                                        ><FaIcon className="fas fa-trash" /></button>
                                                                    )}
                                                                </div>
                                                                <div className="calibration-card-title">{call.operator?.name || '—'}</div>
                                                                <div className="calibration-card-meta">
                                                                    <span>Телефон: {call.phone_number || '—'}</span>
                                                                    <span>Дата: {fmtDate(call.appeal_date)}</span>
                                                                    <span>Оценка админа: {Number(call.score || 0).toFixed(1)}</span>
                                                                </div>
                                                            </button>
                                                        );
                                                    })}
                                                </div>
                                            </>
                                        )}

                                        {calibrationCall && (
                                            <>
                                                <div className="section-divider">Карточка выбранного звонка</div>
                                                <div className="expanded-meta" style={{marginBottom: 10}}>
                                                    <div className="expanded-meta-item"><strong>Оператор:</strong> {calibrationCall.operator?.name || '—'}</div>
                                                    <div className="expanded-meta-item"><strong>Телефон:</strong> {calibrationCall.phone_number || '—'}</div>
                                                    <div className="expanded-meta-item"><strong>Дата:</strong> {fmtDate(calibrationCall.appeal_date)}</div>
                                                    <div className="expanded-meta-item"><strong>Направление:</strong> {calibrationCall.direction?.name || '—'}</div>
                                                    <div className="expanded-meta-item"><strong>Оценка админа:</strong> {Number(calibrationCall.score || 0).toFixed(1)}</div>
                                                    <div className="expanded-meta-item"><strong>Текущий эталон:</strong> {Number(calibrationCall.etalon_score || 0).toFixed(1)}</div>
                                                </div>
                                                {calibrationCall.audio_url && (
                                                    <div className="audio-wrap" style={{maxWidth:520}}>
                                                        <div className="audio-label">Аудиозапись</div>
                                                        <audio key={calibrationCall.id} controls><source src={calibrationCall.audio_url} type="audio/mpeg" /></audio>
                                                    </div>
                                                )}
                                            </>
                                        )}

                                        {calibrationCall && (!calibrationDetail?.can_view_results ? (
                                            <div className="calibration-lock-note">
                                                Результаты участников и проценты калибровки будут доступны после вашей оценки.
                                            </div>
                                        ) : calibrationRows.length === 0 ? (
                                            <div className="calibration-lock-note">Для этого звонка нет критериев оценки.</div>
                                        ) : (
                                            <>
                                                {(calibrationResults?.evaluated_count || 0) === 0 && (
                                                    <div className="calibration-lock-note" style={{marginBottom: 10}}>
                                                        Пока нет оценок супервайзеров для сравнения.
                                                    </div>
                                                )}
                                                <table className="crit-table calibration-result-table">
                                                    <thead>
                                                        <tr>
                                                            <th>Критерий</th>
                                                            <th>Процент калибровки</th>
                                                            <th>Эталон</th>
                                                            <th>Оценка админа ({calibrationRoom?.benchmark_admin?.name || 'Админ'})</th>
                                                            {calibrationEvaluators.map(ev => <th key={ev.id}>{ev.name}</th>)}
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {calibrationRows.map((row) => (
                                                            <tr key={row.criterion_index}>
                                                                <td>
                                                                    {row.criterion_name}
                                                                    {row.is_critical && <span className="calibration-critical-tag">Критичный</span>}
                                                                </td>
                                                                <td>
                                                                    <span className={`calibration-percent ${(row.percent ?? 0) >= 80 ? 'ok' : 'warn'}`}>
                                                                        {row.percent == null ? '—' : `${Number(row.percent).toFixed(1)}%`}
                                                                    </span>
                                                                </td>
                                                                <td>
                                                                    {canManageCalibrationRooms ? (
                                                                        (() => {
                                                                            const criterionMeta = calibrationCriteria[row.criterion_index] || {};
                                                                            const etalonScore = normalizeCalibrationScore(
                                                                                etalonScoresDraft[row.criterion_index] ?? row.etalon?.score ?? row.benchmark?.score ?? 'Correct'
                                                                            );
                                                                            const etalonComment = etalonCommentsDraft[row.criterion_index] ?? '';
                                                                            const isNeg = etalonScore === 'Error' || etalonScore === 'Incorrect' || etalonScore === 'Deficiency';
                                                                            const isCommentRequired = isNeg;
                                                                            return (
                                                                                <div style={{display:'flex', flexDirection:'column', gap:6, minWidth: 210}}>
                                                                                    <div className="score-toggles">
                                                                                        <ScoreToggle
                                                                                            label="Корректно"
                                                                                            value="Correct"
                                                                                            active={etalonScore === 'Correct'}
                                                                                            onClick={() => {
                                                                                                const next = [...etalonScoresDraft];
                                                                                                next[row.criterion_index] = 'Correct';
                                                                                                setEtalonScoresDraft(next);
                                                                                            }}
                                                                                        />
                                                                                        <ScoreToggle
                                                                                            label="N/A"
                                                                                            value="na"
                                                                                            active={etalonScore === 'N/A'}
                                                                                            onClick={() => {
                                                                                                const next = [...etalonScoresDraft];
                                                                                                next[row.criterion_index] = 'N/A';
                                                                                                setEtalonScoresDraft(next);
                                                                                            }}
                                                                                        />
                                                                                        {!row.is_critical && (
                                                                                            <>
                                                                                                <ScoreToggle
                                                                                                    label="Ошибка"
                                                                                                    value="Incorrect"
                                                                                                    active={etalonScore === 'Incorrect'}
                                                                                                    onClick={() => {
                                                                                                        const next = [...etalonScoresDraft];
                                                                                                        next[row.criterion_index] = 'Incorrect';
                                                                                                        setEtalonScoresDraft(next);
                                                                                                    }}
                                                                                                />
                                                                                                {!!criterionMeta?.deficiency && (
                                                                                                    <ScoreToggle
                                                                                                        label="Недочёт"
                                                                                                        value="Deficiency"
                                                                                                        active={etalonScore === 'Deficiency'}
                                                                                                        onClick={() => {
                                                                                                            const next = [...etalonScoresDraft];
                                                                                                            next[row.criterion_index] = 'Deficiency';
                                                                                                            setEtalonScoresDraft(next);
                                                                                                        }}
                                                                                                    />
                                                                                                )}
                                                                                            </>
                                                                                        )}
                                                                                        {row.is_critical && (
                                                                                            <ScoreToggle
                                                                                                label="Критич. ошибка"
                                                                                                value="Error"
                                                                                                active={etalonScore === 'Error'}
                                                                                                onClick={() => {
                                                                                                    const next = [...etalonScoresDraft];
                                                                                                    next[row.criterion_index] = 'Error';
                                                                                                    setEtalonScoresDraft(next);
                                                                                                }}
                                                                                            />
                                                                                        )}
                                                                                    </div>
                                                                                    {(isNeg || String(etalonComment || '').trim().length > 0) && (
                                                                                        <div className="comment-area">
                                                                                            <textarea
                                                                                                className="textarea"
                                                                                                rows={2}
                                                                                                value={etalonComment}
                                                                                                onChange={(e) => {
                                                                                                    const next = [...etalonCommentsDraft];
                                                                                                    next[row.criterion_index] = e.target.value;
                                                                                                    setEtalonCommentsDraft(next);
                                                                                                }}
                                                                                                placeholder={isNeg ? `Укажите причину ошибки в критерии "${row.criterion_name}"` : 'Комментарий (необязательно)'}
                                                                                            />
                                                                                            {isCommentRequired && !String(etalonComment || '').trim() && (
                                                                                                <div className="error-text">Комментарий обязателен</div>
                                                                                            )}
                                                                                        </div>
                                                                                    )}
                                                                                </div>
                                                                            );
                                                                        })()
                                                                    ) : (
                                                                        <>
                                                                            <div>{calibrationScoreLabel(row.etalon?.score ?? row.benchmark?.score)}</div>
                                                                            {(row.etalon?.comment ?? row.benchmark?.comment) && <div className="calibration-cell-comment">{row.etalon?.comment ?? row.benchmark?.comment}</div>}
                                                                        </>
                                                                    )}
                                                                </td>
                                                                <td>
                                                                    <div>{calibrationScoreLabel(row.admin?.score ?? row.benchmark?.score)}</div>
                                                                    {(row.admin?.comment ?? row.benchmark?.comment) && <div className="calibration-cell-comment">{row.admin?.comment ?? row.benchmark?.comment}</div>}
                                                                </td>
                                                                {calibrationEvaluators.map(ev => {
                                                                    const cell = row.by_evaluator?.find(x => Number(x.evaluator_id) === Number(ev.id));
                                                                    return (
                                                                        <td key={ev.id}>
                                                                            {cell ? (
                                                                                <>
                                                                                    <div>{calibrationScoreLabel(cell.score)}</div>
                                                                                    {cell.comment && <div className="calibration-cell-comment">{cell.comment}</div>}
                                                                                </>
                                                                            ) : '—'}
                                                                        </td>
                                                                    );
                                                                })}
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                                <div className="calibration-summary">
                                                    <strong>Общий процент калибровки:</strong>{' '}
                                                    {calibrationResults?.overall_percent == null ? '—' : `${Number(calibrationResults.overall_percent).toFixed(1)}%`}
                                                    {calibrationResults?.critical_mismatch && (
                                                        <span className="calibration-critical-note">
                                                            Есть расхождение по критическому критерию: итоговый процент = 0%.
                                                        </span>
                                                    )}
                                                    {canManageCalibrationRooms && (
                                                        <button
                                                            className="btn btn-primary btn-sm"
                                                            onClick={handleSaveEtalon}
                                                            disabled={!isEtalonDirty || hasEtalonValidationError || isSavingEtalon}
                                                        >
                                                            {isSavingEtalon ? <><span className="spinner" /> Сохранение...</> : <><FaIcon className="fas fa-save" /> Сохранить эталон</>}
                                                        </button>
                                                    )}
                                                </div>
                                                <div className="calibration-general-comments">
                                                    <div className="calibration-general-comments-title">Общий комментарий</div>
                                                    {canManageCalibrationRooms ? (
                                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                                            <textarea
                                                                className="textarea"
                                                                rows={2}
                                                                placeholder="Введите общий комментарий к звонку..."
                                                                value={generalCommentDraft}
                                                                onChange={e => setGeneralCommentDraft(e.target.value)}
                                                                style={{ resize: 'vertical', minHeight: 52, fontSize: 13 }}
                                                            />
                                                            {isGeneralCommentDirty && (
                                                                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                                                                    <button
                                                                        className="btn btn-primary btn-sm"
                                                                        onClick={handleSaveGeneralComment}
                                                                        disabled={isSavingGeneralComment}
                                                                    >
                                                                        {isSavingGeneralComment ? <><span className="spinner" /> Сохранение...</> : <><FaIcon className="fas fa-save" /> Сохранить</>}
                                                                    </button>
                                                                </div>
                                                            )}
                                                            {calibrationCall?.general_comment_updated_by && (
                                                                <div style={{ fontSize: 11, color: 'var(--text-2)' }}>
                                                                    Обновил: {calibrationCall.general_comment_updated_by.name}
                                                                    {calibrationCall.general_comment_updated_at ? ` · ${calibrationCall.general_comment_updated_at}` : ''}
                                                                </div>
                                                            )}
                                                        </div>
                                                    ) : (
                                                        calibrationCall?.general_comment
                                                            ? <div className="calibration-general-comment-text">{calibrationCall.general_comment}</div>
                                                            : <div style={{ fontSize: 13, color: 'var(--text-2)' }}>—</div>
                                                    )}
                                                </div>
                                            </>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                        <div className="panel-footer">
                            <span className="panel-footer-info">
                                {`${calibrationRooms.length} комнат · ${months.find(m=>m.value===selectedMonth)?.label || selectedMonth}`}
                            </span>
                            <div style={{display:'flex',gap:8}}>
                                {canManageCalibrationRooms && (
                                    <button
                                        className="btn btn-primary btn-sm"
                                        onClick={() => setShowCalibrationCreateModal(true)}
                                    >
                                        <FaIcon className="fas fa-plus" /> Создать комнату
                                    </button>
                                )}
                            </div>
                        </div>
                    </>
                )}
            </div>

            {/* Modals */}
            <EvaluationModal
                isOpen={showEvalModal}
                onClose={() => { setShowEvalModal(false); setEditingEval(null); setEvalModalMode('journal'); }}
                onSubmit={handleEvaluateCall}
                directions={directions}
                operator={selectedOperator}
                operators={operators}
                supervisors={supervisors}
                selectedSupervisorId={selectedSupervisor}
                isAdminRole={isAdminRole}
                isSupervisorRole={isSupervisorRole}
                selectedMonth={selectedMonth}
                userId={userId}
                userName={userName}
                existingEvaluation={editingEval}
                submitMode={evalModalMode}
                calibrationRoomId={activeCalibrationRoomId}
                onCalibrationCallCreated={handleCalibrationCallCreated}
            />
            <FeedbackModal
                isOpen={showFeedbackModal}
                onClose={() => { setShowFeedbackModal(false); setFeedbackTargetCall(null); }}
                call={feedbackTargetCall}
                userId={userId}
                onSaved={handleFeedbackSaved}
            />
            <CalibrationRoomCreateModal
                isOpen={showCalibrationCreateModal}
                onClose={() => setShowCalibrationCreateModal(false)}
                userId={userId}
                month={selectedMonth}
                onCreated={handleCalibrationRoomCreated}
            />
            <CalibrationReviewModal
                isOpen={showCalibrationEvalModal}
                onClose={() => setShowCalibrationEvalModal(false)}
                callEntry={calibrationCall}
                userId={userId}
                onSubmitted={handleCalibrationEvaluationSaved}
            />

            {/* Calibration room history modal */}
            {showCalibrationHistoryModal && (
                <div className="modal-backdrop" onClick={() => setShowCalibrationHistoryModal(false)}>
                    <div className="modal" style={{maxWidth: 600}} onClick={e => e.stopPropagation()}>
                        <div className="modal-header">
                            <div>
                                <h2>История комнаты</h2>
                                <div className="modal-header-sub">{calibrationRoom?.room_title || `Комната #${activeCalibrationRoomId}`}</div>
                            </div>
                            <button className="close-btn" onClick={() => setShowCalibrationHistoryModal(false)}><FaIcon className="fas fa-times" /></button>
                        </div>
                        <div className="modal-body">
                            {isCalibrationHistoryLoading ? (
                                <div style={{textAlign:'center', padding:'32px 0', color:'var(--text-2)'}}>
                                    <span className="spinner" /> Загрузка...
                                </div>
                            ) : calibrationHistory.length === 0 ? (
                                <div style={{textAlign:'center', padding:'32px 0', color:'var(--text-2)'}}>Событий не найдено</div>
                            ) : (
                                <div className="calibration-history-list">
                                    {calibrationHistory.map((ev, i) => (
                                        <div key={i} className="calibration-history-item">
                                            <div className="calibration-history-dot" data-type={ev.type} />
                                            <div className="calibration-history-content">
                                                <div className="calibration-history-desc">{ev.description}</div>
                                                <div className="calibration-history-meta">
                                                    <span className="calibration-history-actor">{ev.actor}</span>
                                                    <span className="calibration-history-time">{ev.timestamp}</span>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}

            {/* Version history modal */}
            {showVersionsModal && (
                <div className="modal-backdrop" onClick={() => setShowVersionsModal(false)}>
                    <div className="modal" style={{maxWidth:640}} onClick={e=>e.stopPropagation()}>
                        <div className="modal-header">
                            <div><h2>История версий</h2><div className="modal-header-sub">Все редакции данной оценки</div></div>
                            <button className="close-btn" onClick={() => setShowVersionsModal(false)}><FaIcon className="fas fa-times" /></button>
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
        if (data.type === 'CALL_EVALUATION_INIT') {
            const nextState = { user: data.user || null, initialSelection: data.initialSelection || null };
            writeEmbedState(nextState);
            renderApp(nextState);
        } else if (data.type === 'CALL_EVALUATION_FOCUS') {
            if (typeof window.__callEvaluationFocus === 'function') {
                window.__callEvaluationFocus();
            }
        }
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

