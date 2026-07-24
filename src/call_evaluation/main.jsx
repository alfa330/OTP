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
const CALL_EVALUATION_SECTION_NAMES = Object.freeze({
    analytics: 'Call evaluation analytics',
    calibration: 'Call evaluation calibration',
    journal: 'Call evaluation journal',
    requests: 'Call evaluation requests'
});

const normalizeAnalyticsToken = (value) =>
    String(value || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_/-]+/g, '_')
        .replace(/_+/g, '_')
        .replace(/^_+|_+$/g, '');

const formatAnalyticsName = (value) => {
    const normalized = normalizeAnalyticsToken(value).replace(/[\/_-]+/g, ' ').trim();
    return normalized ? normalized.replace(/\b\w/g, (char) => char.toUpperCase()) : 'Unknown';
};

const buildCallEvaluationAnalyticsPageParams = (sectionId) => {
    const normalizedSection = normalizeAnalyticsToken(sectionId) || 'journal';
    const subviewId = `call_evaluation_${normalizedSection}`;
    const pagePath = `/app/call_evaluation/${encodeURIComponent(subviewId)}`;
    const origin = typeof window !== 'undefined' ? window.location.origin : '';

    return {
        page_location: `${origin}${pagePath}`,
        page_path: pagePath,
        page_title: `Call evaluation - ${CALL_EVALUATION_SECTION_NAMES[normalizedSection] || formatAnalyticsName(subviewId)}`
    };
};

const notifyEmbeddedCallEvaluationSectionView = ({ section = 'journal', role = '' } = {}) => {
    if (typeof window === 'undefined') return false;
    if (!window.parent || window.parent === window) return false;

    const sectionId = normalizeAnalyticsToken(section) || 'journal';
    const roleId = normalizeAnalyticsToken(role === 'supervisor' ? 'sv' : role);
    try {
        window.parent.postMessage({
            type: 'CALL_EVALUATION_SECTION_VIEW',
            section: sectionId,
            role: roleId
        }, window.location.origin);
        return true;
    } catch (error) {
        console.warn('Failed to notify parent about call evaluation analytics event:', error);
        return false;
    }
};

const trackCallEvaluationAppView = ({ section = 'journal', role = '' } = {}) => {
    if (typeof window === 'undefined') return false;

    const sectionId = normalizeAnalyticsToken(section) || 'journal';
    const pageParams = buildCallEvaluationAnalyticsPageParams(sectionId);
    // Держим document.title в соответствии с активным разделом, чтобы GA привязывал
    // автоматически собираемые события к нужному page_title, а не к статичному заголовку.
    if (typeof document !== 'undefined' && pageParams.page_title) {
        document.title = pageParams.page_title;
    }

    // В iframe аналитику владеет родительское окно (через postMessage) — page_view отсюда не шлём.
    if (window.parent && window.parent !== window) return false;
    if (typeof window.gtag !== 'function') return false;

    const roleId = normalizeAnalyticsToken(role === 'supervisor' ? 'sv' : role);
    const subviewId = `call_evaluation_${sectionId}`;
    const params = {
        app_view_id: 'call_evaluation',
        app_view_name: 'Call evaluation',
        app_subview_id: subviewId,
        app_subview_name: CALL_EVALUATION_SECTION_NAMES[sectionId] || formatAnalyticsName(subviewId),
        ...pageParams
    };

    if (roleId) {
        params.app_user_role = roleId;
    }

    try {
        window.gtag('event', 'app_view', params);
        window.gtag('event', 'page_view', params);
        return true;
    } catch (error) {
        console.warn('Failed to send call evaluation analytics event:', error);
        return false;
    }
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

const resolveAvatarUrl = (value) => {
    const raw = String(value || '').trim();
    if (!raw) return '';
    if (/^(https?:|data:|blob:)/i.test(raw)) return raw;
    if (raw.startsWith('/')) return `${API_BASE_URL}${raw}`;
    return `${API_BASE_URL}/${raw.replace(/^\/+/, '')}`;
};

const OperatorAvatar = ({ operator, size = 32 }) => {
    const [failed, setFailed] = useState(false);
    const name = operator?.name || operator?.operator_name || '';
    const avatarUrl = resolveAvatarUrl(operator?.avatar_url || operator?.avatarUrl || operator?.photo_url);
    const initials = (name || 'U').charAt(0).toUpperCase();

    return (
        <div className="analytics-avatar" style={{ width: size, height: size }}>
            {avatarUrl && !failed ? (
                <img
                    className="analytics-avatar-img"
                    src={avatarUrl}
                    alt={name || 'avatar'}
                    loading="lazy"
                    decoding="async"
                    referrerPolicy="no-referrer"
                    onError={() => setFailed(true)}
                />
            ) : (
                initials
            )}
        </div>
    );
};

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

const hydrateAuthSnapshot = (auth = null) => {
    if (!auth || typeof auth !== 'object') return false;

    const transport = normalizeClientAuthTransport(auth.transport || auth.auth_transport);
    const accessToken = String(auth.accessToken || auth.access_token || '').trim();
    const refreshToken = String(auth.refreshToken || auth.refresh_token || '').trim();
    const sessionStorageRef = safeGetBrowserStorage('sessionStorage');
    const localStorageRef = safeGetBrowserStorage('localStorage');

    if (accessToken) {
        authRuntimeState.accessToken = accessToken;
        safeStorageSetItem(sessionStorageRef, ACCESS_TOKEN_STORAGE_KEY, accessToken);
        if (shouldUseLegacyMobileBearerStorage()) {
            safeStorageSetItem(localStorageRef, ACCESS_TOKEN_STORAGE_KEY, accessToken);
        }
    }

    if (refreshToken) {
        authRuntimeState.refreshToken = refreshToken;
        safeStorageSetItem(sessionStorageRef, REFRESH_TOKEN_STORAGE_KEY, refreshToken);
        if (shouldUseLegacyMobileBearerStorage()) {
            safeStorageSetItem(localStorageRef, REFRESH_TOKEN_STORAGE_KEY, refreshToken);
        }
    }

    if (accessToken || refreshToken) {
        setStoredAuthTransport('bearer');
        return true;
    }

    if (transport) {
        setStoredAuthTransport(transport);
        return true;
    }

    return false;
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
        code === 'REFRESH_TOKEN_MISMATCH' ||
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

// Аудио неоценённого (импортированного) звонка из Binotel или Oktell.
// Не кэшируем по общему audioUrlCache: id импортированного звонка живёт в своей
// таблице и может пересекаться с id обычной оценки.
const getImportedAudioUrl = async (importedId, userId) => {
    try {
        const r = await authFetch(`${API_BASE_URL}/api/imported_calls/${importedId}/audio`, { headers: { 'X-User-Id': userId } });
        if (!r.ok) return null;
        const d = await r.json();
        return d?.url || null;
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

// Страница живёт в iframe, где window.showToast родителя недоступен — раньше
// ВСЕ тосты журнала («Добавлено в журнал», ошибки сохранения…) молча уходили
// в консоль. Собственный минимальный тост-слой (стили — .ce-toast в styles.css).
if (typeof window !== 'undefined' && typeof document !== 'undefined'
        && typeof window.showToast !== 'function') {
    window.showToast = (message, type = 'info') => {
        let host = document.getElementById('ce-toast-host');
        if (!host) {
            host = document.createElement('div');
            host.id = 'ce-toast-host';
            document.body.appendChild(host);
        }
        const el = document.createElement('div');
        el.className = `ce-toast ce-toast-${type}`;
        el.textContent = String(message ?? '');
        host.appendChild(el);
        requestAnimationFrame(() => el.classList.add('ce-toast-in'));
        window.setTimeout(() => {
            el.classList.remove('ce-toast-in');
            window.setTimeout(() => el.remove(), 250);
        }, type === 'error' ? 6000 : 3500);
    };
}

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
    return s === 'fired' || s === 'dismissal' || s === 'dismissed' || s === 'terminated' || s === 'уволен';
};
const hasAnyEvaluationIndicators = (operatorRow) => {
    if (!operatorRow || typeof operatorRow !== 'object') return false;
    if (operatorRow.has_evaluation_data === true || operatorRow.hasEvaluationData === true) return true;

    const numericKeys = [
        'call_count',
        'evaluation_row_count',
        'feedback_count',
        'feedback_overdue_count',
        'feedback_pending_count',
    ];
    for (const key of numericKeys) {
        const value = Number(operatorRow[key]);
        if (Number.isFinite(value) && value > 0) return true;
    }

    const avgScore = Number(operatorRow.avg_score);
    return Number.isFinite(avgScore) && avgScore > 0;
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

const normalizeReevaluationRequestRole = (value) => {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'supervisor') return 'sv';
    if (normalized === 'super_admin') return 'admin';
    return normalized;
};

const getReevaluationRequestStatus = (call) => {
    if (!call?.sv_request) return 'none';
    if (call?.sv_request_approved) return 'approved';
    if (call?.sv_request_rejected) return 'rejected';
    return 'pending';
};

const getReevaluationRequestRoleLabel = (role) => {
    const normalized = normalizeReevaluationRequestRole(role);
    if (normalized === 'operator') return 'Оператор';
    if (normalized === 'sv') return 'Супервайзер';
    if (normalized === 'admin') return 'Администратор';
    return 'Сотрудник';
};

const buildReevaluationRequestTooltip = (call) => {
    if (!call?.sv_request) return '';
    const lines = [
        call.sv_request_by_name
            ? `Запросил: ${call.sv_request_by_name}${call.sv_request_by_role ? ` (${getReevaluationRequestRoleLabel(call.sv_request_by_role)})` : ''}`
            : null,
        call.sv_request_at ? `Создан: ${call.sv_request_at}` : null,
        call.sv_request_comment ? `Комментарий: ${call.sv_request_comment}` : null,
        call.sv_request_approved && call.sv_request_approved_by_name
            ? `Одобрил: ${call.sv_request_approved_by_name}${call.sv_request_approved_at ? ` (${call.sv_request_approved_at})` : ''}`
            : null,
        call.sv_request_approved && call.sv_request_approve_comment
            ? `Комментарий: ${call.sv_request_approve_comment}`
            : null,
        call.sv_request_rejected && call.sv_request_rejected_by_name
            ? `Отклонил: ${call.sv_request_rejected_by_name}${call.sv_request_rejected_at ? ` (${call.sv_request_rejected_at})` : ''}`
            : null,
        call.sv_request_rejected && call.sv_request_reject_comment
            ? `Причина: ${call.sv_request_reject_comment}`
            : null
    ].filter(Boolean);
    return lines.join('\n');
};

const getReevaluationRequestStatusMeta = (call) => {
    const status = getReevaluationRequestStatus(call);
    if (status === 'approved') {
        return { status, label: 'Одобрено', color: 'var(--green)', icon: 'fas fa-check-circle' };
    }
    if (status === 'rejected') {
        return { status, label: 'Отклонено', color: 'var(--red)', icon: 'fas fa-times-circle' };
    }
    if (status === 'pending') {
        return { status, label: 'На рассмотрении', color: 'var(--amber)', icon: 'fas fa-clock' };
    }
    return { status: 'none', label: 'Нет запроса', color: 'var(--text-3)', icon: 'fas fa-minus-circle' };
};

const getReevaluationRequestOutcomeMeta = (call) => {
    if (call?.correction_call_id) {
        return { label: 'Переоценено', color: 'var(--accent)', icon: 'fas fa-redo' };
    }
    return getReevaluationRequestStatusMeta(call);
};

const SvRequestButton = ({ call, userId, userRole, isAdminRole = false, fetchEvaluations, onReevaluate, onUpdated, pendingAdminMode = 'default' }) => {
    const [showModal, setShowModal] = useState(false);
    const [showRejectModal, setShowRejectModal] = useState(false);
    const [showApproveModal, setShowApproveModal] = useState(false);
    const [comment, setComment] = useState('');
    const [decisionComment, setDecisionComment] = useState('');
    const [loading, setLoading] = useState(false);
    const normalizedRole = normalizeReevaluationRequestRole(userRole);
    const isSv = normalizedRole === 'sv';
    const isAdmin = isAdminRole || normalizedRole === 'admin';
    const status = getReevaluationRequestStatus(call);
    const tooltipText = buildReevaluationRequestTooltip(call);
    const canSubmitRequest = isSv && (status === 'none' || status === 'rejected');

    const submit = async () => {
        setLoading(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation/sv_request`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-User-Id': userId },
                body: JSON.stringify({ call_id: call.id, comment })
            });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || 'Не удалось отправить запрос');
            await fetchEvaluations?.({ force: true });
            await onUpdated?.();
            setShowModal(false);
            setComment('');
            emitCallEvaluationToast('Заявка отправлена', 'success');
        } catch (e) {
            emitCallEvaluationToast('Ошибка: ' + e.message, 'error');
        } finally {
            setLoading(false);
        }
    };

    const decideRequest = async (decision) => {
        setLoading(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation/request_decision`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-User-Id': userId },
                body: JSON.stringify({ call_id: call.id, decision, comment: decisionComment })
            });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || 'Не удалось обновить заявку');
            await fetchEvaluations?.({ force: true });
            await onUpdated?.();
            setShowRejectModal(false);
            setShowApproveModal(false);
            setDecisionComment('');
            emitCallEvaluationToast(decision === 'approved' ? 'Заявка одобрена' : 'Заявка отклонена', 'success');
        } catch (e) {
            emitCallEvaluationToast('Ошибка: ' + e.message, 'error');
        } finally {
            setLoading(false);
        }
    };

    if (canSubmitRequest) return (
        <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {status === 'rejected' && tooltipText ? (
                    <HoverTooltip text={tooltipText}>
                        <FaIcon className="fas fa-info-circle" style={{ color: 'var(--red)', cursor: 'pointer' }} />
                    </HoverTooltip>
                ) : null}
                <button className="btn btn-amber btn-sm" onClick={e => { e.stopPropagation(); setShowModal(true); }}>
                    {status === 'rejected' ? 'Повторить запрос' : 'Запрос'}
                </button>
            </div>
            {showModal && (
                <div className="modal-backdrop" onClick={e => { e.stopPropagation(); setShowModal(false); }}>
                    <div className="modal request-modal" onClick={e => e.stopPropagation()}>
                        <div className="modal-header">
                            <div><h2>Запрос на переоценку</h2><div className="modal-header-sub">Call ID: {call.id}</div></div>
                            <button className="close-btn" onClick={() => setShowModal(false)}><FaIcon className="fas fa-times" /></button>
                        </div>
                        <div className="modal-body">
                            <div className="field">
                                <label className="label">Комментарий</label>
                                <textarea className="textarea" value={comment} onChange={e => setComment(e.target.value)} placeholder="Опишите причину запроса..." />
                            </div>
                        </div>
                        <div className="modal-footer">
                            <button className="btn btn-secondary" onClick={() => setShowModal(false)}>Отмена</button>
                            <button className="btn btn-amber" onClick={submit} disabled={loading}>
                                {loading ? <><span className="spinner" style={{ borderTopColor: 'var(--amber)' }} /> Отправка...</> : 'Отправить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );

    if (status === 'pending') {
        if (isAdmin) {
            return (
                <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        {pendingAdminMode !== 'buttons_only' ? (
                            <HoverTooltip text={tooltipText || 'Запрос ожидает решения'}>
                                <span style={{ fontSize: 13, color: 'var(--amber)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                    <FaIcon className="fas fa-clock" style={{ fontSize: 11 }} /> Ожидает
                                </span>
                            </HoverTooltip>
                        ) : null}
                        <button className="btn btn-green btn-sm" onClick={e => { e.stopPropagation(); setDecisionComment(''); setShowApproveModal(true); }} disabled={loading}>
                            Принять
                        </button>
                        <button className="btn btn-danger btn-sm" onClick={e => { e.stopPropagation(); setDecisionComment(''); setShowRejectModal(true); }} disabled={loading}>
                            Отклонить
                        </button>
                        {pendingAdminMode === 'buttons_only' && tooltipText ? (
                            <HoverTooltip text={tooltipText}>
                                <span style={{ color: 'var(--text-2)', display: 'inline-flex', alignItems: 'center', cursor: 'pointer' }}>
                                    <FaIcon className="fas fa-info-circle" />
                                </span>
                            </HoverTooltip>
                        ) : null}
                    </div>
                    {showApproveModal && (
                        <div className="modal-backdrop" onClick={e => { e.stopPropagation(); setShowApproveModal(false); }}>
                            <div className="modal request-modal" onClick={e => e.stopPropagation()}>
                                <div className="modal-header">
                                    <div><h2>Одобрить запрос</h2><div className="modal-header-sub">Call ID: {call.id}</div></div>
                                    <button className="close-btn" onClick={() => setShowApproveModal(false)}><FaIcon className="fas fa-times" /></button>
                                </div>
                                <div className="modal-body">
                                    <div className="field">
                                        <label className="label">Комментарий</label>
                                        <textarea className="textarea" value={decisionComment} onChange={e => setDecisionComment(e.target.value)} placeholder="При необходимости добавьте комментарий..." />
                                    </div>
                                </div>
                                <div className="modal-footer">
                                    <button className="btn btn-secondary" onClick={() => setShowApproveModal(false)}>Отмена</button>
                                    <button className="btn btn-green" onClick={() => void decideRequest('approved')} disabled={loading}>
                                        {loading ? <><span className="spinner" style={{ borderTopColor: 'var(--green)' }} /> Одобрение...</> : 'Одобрить'}
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                    {showRejectModal && (
                        <div className="modal-backdrop" onClick={e => { e.stopPropagation(); setShowRejectModal(false); }}>
                            <div className="modal request-modal" onClick={e => e.stopPropagation()}>
                                <div className="modal-header">
                                    <div><h2>Отклонить запрос</h2><div className="modal-header-sub">Call ID: {call.id}</div></div>
                                    <button className="close-btn" onClick={() => setShowRejectModal(false)}><FaIcon className="fas fa-times" /></button>
                                </div>
                                <div className="modal-body">
                                    <div className="field">
                                        <label className="label">Причина отклонения</label>
                                        <textarea className="textarea" value={decisionComment} onChange={e => setDecisionComment(e.target.value)} placeholder="При необходимости укажите причину..." />
                                    </div>
                                </div>
                                <div className="modal-footer">
                                    <button className="btn btn-secondary" onClick={() => setShowRejectModal(false)}>Отмена</button>
                                    <button className="btn btn-danger" onClick={() => void decideRequest('rejected')} disabled={loading}>
                                        {loading ? <><span className="spinner" style={{ borderTopColor: 'var(--red)' }} /> Отклонение...</> : 'Отклонить'}
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                </>
            );
        }

        return (
            <HoverTooltip text={tooltipText || 'Запрос на рассмотрении'}>
                <span style={{ fontSize: 13, color: 'var(--amber)', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <FaIcon className="fas fa-clock" style={{ fontSize: 11 }} /> Ожидает
                </span>
            </HoverTooltip>
        );
    }

    if (status === 'approved') {
        if (call?.correction_call_id) {
            return (
                <HoverTooltip text={tooltipText || 'Переоценка уже выполнена'}>
                    <span style={{ fontSize: 13, color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: 4 }}>
                        <FaIcon className="fas fa-redo" style={{ fontSize: 11 }} /> Переоценено
                    </span>
                </HoverTooltip>
            );
        }

        if (isSv) {
            return (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <HoverTooltip text={tooltipText || 'Запрос одобрен'}>
                        <FaIcon className="fas fa-info-circle" style={{ color: 'var(--green)', cursor: 'pointer' }} />
                    </HoverTooltip>
                    <button className="btn btn-primary btn-sm" onClick={e => { e.stopPropagation(); onReevaluate(); }}>Переоценить</button>
                </div>
            );
        }

        return (
            <HoverTooltip text={tooltipText || 'Запрос одобрен'}>
                <span style={{ fontSize: 13, color: 'var(--green)', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <FaIcon className="fas fa-check-circle" style={{ fontSize: 11 }} /> Одобрено
                </span>
            </HoverTooltip>
        );
    }

    if (status === 'rejected') {
        return (
            <HoverTooltip text={tooltipText || 'Запрос отклонён'}>
                <span style={{ fontSize: 13, color: 'var(--red)', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <FaIcon className="fas fa-times-circle" style={{ fontSize: 11 }} /> Отклонён
                </span>
            </HoverTooltip>
        );
    }

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
    const [skipTraining, setSkipTraining] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const scoreValue = Number(call?.totalScore ?? call?._rawEvaluation?.score);
    const canSkipTraining = Number.isFinite(scoreValue) && scoreValue >= 90;

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
        const existingTrainingId = Number(currentFeedback?.training_id || 0);
        setSkipTraining(Boolean(canSkipTraining && currentFeedback?.id && existingTrainingId <= 0));
    }, [isOpen, call, canSkipTraining]);

    if (!isOpen || !call) return null;

    const hasExistingFeedback = !!call?.feedback?.id;
    const requiresTrainingFields = !(canSkipTraining && skipTraining);
    const isDisabled =
        isSubmitting ||
        !feedbackComment.trim() ||
        (
            requiresTrainingFields && (
                !deliveryComment.trim() ||
                !feedbackDate ||
                !startTime ||
                !endTime
            )
        );

    const submit = async () => {
        if (isDisabled) return;
        if (requiresTrainingFields && endTime <= startTime) {
            emitCallEvaluationToast('Время окончания должно быть позже времени начала', 'error');
            return;
        }

        const payload = {
            feedback_comment: feedbackComment.trim(),
            delivery_comment: deliveryComment.trim(),
            date: feedbackDate,
            start_time: startTime,
            end_time: endTime
        };
        if (!requiresTrainingFields) {
            payload.delivery_comment = payload.delivery_comment || 'Тренинг не требуется.';
            payload.date = payload.date || new Date().toISOString().slice(0, 10);
            payload.start_time = payload.start_time || '00:00';
            payload.end_time = payload.end_time || '00:01';
        }

        setIsSubmitting(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluations/${call.id}/feedback`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Id': userId
                },
                body: JSON.stringify(payload)
            });
            const d = await r.json();
            if (!r.ok || d.status !== 'success') {
                throw new Error(d?.error || 'Не удалось сохранить обратную связь');
            }

            if (!requiresTrainingFields) {
                const trainingId = Number(d?.feedback?.training_id || call?.feedback?.training_id || 0);
                if (trainingId > 0) {
                    const removeTrainingResponse = await authFetch(`${API_BASE_URL}/api/trainings/${trainingId}`, {
                        method: 'DELETE',
                        headers: {
                            'X-User-Id': userId
                        }
                    });
                    const removeTrainingPayload = await readJsonSafe(removeTrainingResponse);
                    if (!removeTrainingResponse.ok || (removeTrainingPayload?.status && removeTrainingPayload.status !== 'success')) {
                        throw new Error(removeTrainingPayload?.error || 'ОС сохранена, но удалить тренинг не удалось');
                    }
                }
                if (d?.feedback) d.feedback.training_id = null;
            }

            emitCallEvaluationToast(
                !requiresTrainingFields
                    ? (hasExistingFeedback ? 'Обратная связь обновлена без тренинга' : 'Обратная связь сохранена без тренинга')
                    : (hasExistingFeedback ? 'Обратная связь обновлена' : 'Обратная связь добавлена'),
                'success'
            );
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
                    {canSkipTraining && (
                        <label style={{display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)', marginBottom: 4}}>
                            <input
                                type="checkbox"
                                checked={!!skipTraining}
                                onChange={e => setSkipTraining(!!e.target.checked)}
                            />
                            Не добавлять тренинг
                        </label>
                    )}
                    {requiresTrainingFields ? (
                        <>
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
                        </>
                    ) : (
                        <div style={{marginTop: 10, fontSize: 12, color:'var(--text-2)'}}>
                            При сохранении будет добавлен только комментарий ОС, тренинг создан не будет.
                        </div>
                    )}
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

// ─── Batch Feedback Modal ──────────────────────────────
// Дать ОС сразу по нескольким оценкам одного оператора: единое время/способ
// проведения тренинга на всех + индивидуальная ОС по каждой оценке.
const BatchFeedbackModal = ({
    isOpen,
    onClose,
    calls,
    userId,
    onSaved
}) => {
    const [deliveryComment, setDeliveryComment] = useState('');
    const [feedbackDate, setFeedbackDate] = useState('');
    const [startTime, setStartTime] = useState('');
    const [endTime, setEndTime] = useState('');
    const [comments, setComments] = useState({});
    const [audioUrls, setAudioUrls] = useState({});
    const [isSubmitting, setIsSubmitting] = useState(false);

    useEffect(() => {
        if (!isOpen) return;
        const now = new Date();
        setDeliveryComment('');
        setFeedbackDate(now.toISOString().slice(0, 10));
        setStartTime('');
        setEndTime('');
        const initial = {};
        (calls || []).forEach(c => { initial[c.id] = ''; });
        setComments(initial);
    }, [isOpen, calls]);

    // Подгружаем аудиозаписи выбранных оценок, чтобы прослушать их при разборе.
    useEffect(() => {
        if (!isOpen) { setAudioUrls({}); return; }
        let cancelled = false;
        setAudioUrls({});
        (calls || []).forEach(c => {
            if (!c?.directions?.[0]?.hasFileUpload) return;
            if (c.audioUrl) {
                setAudioUrls(prev => ({ ...prev, [c.id]: c.audioUrl }));
                return;
            }
            getAudioUrl(c.id, userId).then(url => {
                if (!cancelled && url) setAudioUrls(prev => ({ ...prev, [c.id]: url }));
            });
        });
        return () => { cancelled = true; };
    }, [isOpen, calls, userId]);

    if (!isOpen || !Array.isArray(calls) || calls.length === 0) return null;

    const allCommentsFilled = calls.every(c => String(comments[c.id] || '').trim());
    const isDisabled =
        isSubmitting ||
        !deliveryComment.trim() ||
        !feedbackDate ||
        !startTime ||
        !endTime ||
        !allCommentsFilled;

    const submit = async () => {
        if (isDisabled) return;
        if (endTime <= startTime) {
            emitCallEvaluationToast('Время окончания должно быть позже времени начала', 'error');
            return;
        }

        const payload = {
            delivery_comment: deliveryComment.trim(),
            date: feedbackDate,
            start_time: startTime,
            end_time: endTime,
            items: calls.map(c => ({
                call_id: c.id,
                feedback_comment: String(comments[c.id] || '').trim()
            }))
        };

        setIsSubmitting(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluations/feedback/batch`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Id': userId
                },
                body: JSON.stringify(payload)
            });
            const d = await readJsonSafe(r);
            if (!r.ok || d?.status !== 'success') {
                throw new Error(d?.error || 'Не удалось сохранить обратную связь');
            }
            emitCallEvaluationToast(`Обратная связь добавлена для ${d.created ?? calls.length} оц.`, 'success');
            if (typeof onSaved === 'function') onSaved();
            onClose?.();
        } catch (e) {
            emitCallEvaluationToast(`Ошибка: ${e.message}`, 'error');
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="modal-backdrop" onClick={onClose}>
            <div className="modal request-modal" style={{maxWidth: 640}} onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <div>
                        <h2>Пакетная обратная связь</h2>
                        <div className="modal-header-sub">Выбрано оценок: {calls.length} · единый тренинг на всех</div>
                    </div>
                    <button className="close-btn" onClick={onClose}><FaIcon className="fas fa-times" /></button>
                </div>
                <div className="modal-body">
                    <div className="field">
                        <label className="label">Как проведена обратная связь</label>
                        <textarea
                            className="textarea"
                            rows={2}
                            placeholder="Например: индивидуальный разбор, прослушивание звонков, чек-лист ошибок"
                            value={deliveryComment}
                            onChange={e => setDeliveryComment(e.target.value)}
                        />
                    </div>
                    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:10}}>
                        <div className="field" style={{marginBottom: 0}}>
                            <label className="label">Дата</label>
                            <input className="input" type="date" value={feedbackDate} onChange={e => setFeedbackDate(e.target.value)} />
                        </div>
                        <div className="field" style={{marginBottom: 0}}>
                            <label className="label">Начало</label>
                            <input className="input" type="time" value={startTime} onChange={e => setStartTime(e.target.value)} />
                        </div>
                        <div className="field" style={{marginBottom: 0}}>
                            <label className="label">Окончание</label>
                            <input className="input" type="time" value={endTime} onChange={e => setEndTime(e.target.value)} />
                        </div>
                    </div>
                    <div style={{marginTop:14, marginBottom:8, fontWeight:600, color:'var(--text)'}}>Обратная связь по каждой оценке</div>
                    <div style={{display:'flex',flexDirection:'column',gap:12,maxHeight:'40vh',overflowY:'auto',paddingRight:4}}>
                        {calls.map((c, i) => (
                            <div key={c.id} style={{border:'1px solid var(--border)',borderRadius:'var(--radius)',padding:'10px 12px',background:'var(--surface-2)'}}>
                                <div style={{display:'flex',flexWrap:'wrap',alignItems:'center',gap:'4px 12px',marginBottom:8,fontSize:12,color:'var(--text-2)'}}>
                                    <strong style={{color:'var(--text)'}}>#{i+1}</strong>
                                    <span style={{fontFamily:'var(--font-mono)'}}>{c.phoneNumber || '—'}</span>
                                    <span>{c.selectedDirection || '—'}</span>
                                    {c.totalScore != null && <span>Балл: {Math.round(c.totalScore)}</span>}
                                    <span>Call ID: {c.id}</span>
                                </div>
                                {audioUrls[c.id] && (
                                    <div style={{marginBottom:8}}>
                                        <audio controls style={{width:'100%'}}><source src={audioUrls[c.id]} type="audio/mpeg" /></audio>
                                    </div>
                                )}
                                <textarea
                                    className="textarea"
                                    rows={2}
                                    placeholder="Что было донесено оператору по этой оценке"
                                    value={comments[c.id] || ''}
                                    onChange={e => setComments(prev => ({...prev, [c.id]: e.target.value}))}
                                />
                            </div>
                        ))}
                    </div>
                    <div style={{marginTop: 10, fontSize: 12, color:'var(--text-2)'}}>
                        При сохранении будет создан один общий тренинг
                        <strong style={{color:'var(--text)'}}> «Тренинг по качеству. Разбор ошибок»</strong> на всё выбранное.
                    </div>
                </div>
                <div className="modal-footer">
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary" onClick={submit} disabled={isDisabled}>
                        {isSubmitting
                            ? <><span className="spinner" /> Сохранение...</>
                            : `Сохранить ОС (${calls.length})`}
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

// ─── Custom range picker (macOS/iOS-стиль, в стиле сайта) + «Случайный звонок» ───
const RC_MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
const RC_WEEKDAYS_RU = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
const rcPad = (n) => String(n).padStart(2, '0');
const rcToKey = (d) => `${d.getFullYear()}-${rcPad(d.getMonth() + 1)}-${rcPad(d.getDate())}`;
const rcParseKey = (key) => {
    const m = typeof key === 'string' && key.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return Number.isNaN(d.getTime()) ? null : d;
};
const rcAddDays = (date, n) => { const r = new Date(date); r.setDate(r.getDate() + n); return r; };
const rcWeekStart = (date) => { const day = date.getDay() || 7; const s = new Date(date); s.setDate(date.getDate() - (day - 1)); s.setHours(0, 0, 0, 0); return s; };
const rcFormatRu = (key) => { const d = rcParseKey(key); return d ? `${rcPad(d.getDate())}.${rcPad(d.getMonth() + 1)}.${d.getFullYear()}` : '—'; };
const rcSpanDays = (start, end) => { const s = rcParseKey(start); const e = rcParseKey(end); return (s && e) ? Math.round((e - s) / 86400000) + 1 : 0; };
const rcMonthStartKey = (mo) => `${mo}-01`;
const rcMonthEndKey = (mo) => { const y = Number(mo.slice(0, 4)); const m = Number(mo.slice(5, 7)); return `${mo}-${rcPad(new Date(y, m, 0).getDate())}`; };

const RcRangeCalendar = ({ value, onChange, maxKey, minKey }) => {
    const todayKey = rcToKey(new Date());
    const start = value?.start || todayKey;
    const end = value?.end || start;
    const [activeEdge, setActiveEdge] = useState('start');
    const [calMonth, setCalMonth] = useState(() => {
        const base = rcParseKey(start) || new Date();
        return new Date(base.getFullYear(), base.getMonth(), 1);
    });

    const selectDay = (key) => {
        if (maxKey && key > maxKey) return;
        if (minKey && key < minKey) return;
        if (activeEdge === 'start') {
            onChange?.({ start: key, end: key > end ? key : end });
            setActiveEdge('end');
            return;
        }
        if (key < start) onChange?.({ start: key, end: start });
        else onChange?.({ start, end: key });
        setActiveEdge('start');
    };

    const applyPreset = (preset) => {
        const now = new Date();
        let s; let e;
        if (preset === 'thisMonth') { s = new Date(now.getFullYear(), now.getMonth(), 1); e = new Date(now.getFullYear(), now.getMonth() + 1, 0); }
        else if (preset === 'prevMonth') { s = new Date(now.getFullYear(), now.getMonth() - 1, 1); e = new Date(now.getFullYear(), now.getMonth(), 0); }
        else if (preset === 'last7') { s = rcAddDays(now, -6); e = now; }
        else { s = rcAddDays(now, -29); e = now; }
        if (e > now) e = now;
        let sKey = rcToKey(s);
        let eKey = rcToKey(e);
        if (minKey && sKey < minKey) sKey = minKey;  // держим пресет в границах [minKey, maxKey]
        if (maxKey && eKey > maxKey) eKey = maxKey;
        if (minKey && eKey < minKey) eKey = minKey;
        if (sKey > eKey) sKey = eKey;
        onChange?.({ start: sKey, end: eKey });
        setCalMonth(new Date(s.getFullYear(), s.getMonth(), 1));
        setActiveEdge('start');
    };

    const cells = (() => {
        const monthStart = new Date(calMonth.getFullYear(), calMonth.getMonth(), 1);
        const gridStart = rcWeekStart(monthStart);
        return Array.from({ length: 42 }).map((_, idx) => {
            const date = rcAddDays(gridStart, idx);
            const key = rcToKey(date);
            return {
                date, key,
                inMonth: date.getMonth() === monthStart.getMonth(),
                isStart: key === start, isEnd: key === end,
                inRange: key > start && key < end,
                isToday: key === todayKey,
                disabled: !!((maxKey && key > maxKey) || (minKey && key < minKey)),
            };
        });
    })();

    const presets = [
        { key: 'thisMonth', label: 'Этот месяц' },
        { key: 'prevMonth', label: 'Прошлый' },
        { key: 'last7', label: '7 дней' },
        { key: 'last30', label: '30 дней' },
    ];

    return (
        <div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
                {presets.map(p => (
                    <button key={p.key} type="button" className="btn btn-secondary btn-sm" onClick={() => applyPreset(p.key)}>{p.label}</button>
                ))}
            </div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
                {[{ k: 'start', t: 'Начало', v: start }, { k: 'end', t: 'Конец', v: end }].map(item => (
                    <button key={item.k} type="button" onClick={() => setActiveEdge(item.k)}
                        style={{ flex: 1, textAlign: 'left', padding: '8px 12px', borderRadius: 'var(--radius)', cursor: 'pointer',
                            border: `1px solid ${activeEdge === item.k ? 'var(--accent)' : 'var(--border-strong)'}`,
                            background: activeEdge === item.k ? 'var(--accent-light)' : 'var(--surface)' }}>
                        <span style={{ display: 'block', fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-3)' }}>{item.t}</span>
                        <span style={{ display: 'block', marginTop: 2, fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{rcFormatRu(item.v)}</span>
                    </button>
                ))}
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', overflow: 'hidden', background: 'var(--surface)' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', borderBottom: '1px solid var(--border)', background: 'var(--surface-2)' }}>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setCalMonth(p => new Date(p.getFullYear(), p.getMonth() - 1, 1))} aria-label="Предыдущий месяц"><FaIcon className="fas fa-angle-left" /></button>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{RC_MONTHS_RU[calMonth.getMonth()]} {calMonth.getFullYear()}</span>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setCalMonth(p => new Date(p.getFullYear(), p.getMonth() + 1, 1))} aria-label="Следующий месяц"><FaIcon className="fas fa-angle-right" /></button>
                </div>
                <div style={{ padding: '8px 10px' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 4, marginBottom: 4 }}>
                        {RC_WEEKDAYS_RU.map(w => (
                            <div key={w} style={{ textAlign: 'center', fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-3)' }}>{w}</div>
                        ))}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 4 }}>
                        {cells.map(cell => {
                            const endpoint = cell.isStart || cell.isEnd;
                            return (
                                <button key={cell.key} type="button" disabled={cell.disabled} onClick={() => selectDay(cell.key)} title={rcFormatRu(cell.key)}
                                    style={{ height: 34, borderRadius: endpoint ? 9 : 7, border: 'none', fontSize: 13, fontWeight: 600,
                                        cursor: cell.disabled ? 'not-allowed' : 'pointer', transition: 'background .12s, color .12s',
                                        opacity: cell.disabled ? 0.35 : 1,
                                        background: endpoint ? 'var(--accent)' : cell.inRange ? 'var(--accent-light)' : 'transparent',
                                        color: endpoint ? '#fff' : cell.inMonth ? 'var(--text)' : 'var(--text-3)',
                                        boxShadow: cell.isToday && !endpoint ? 'inset 0 0 0 1px var(--border-strong)' : 'none' }}>
                                    {cell.date.getDate()}
                                </button>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
};

// Звонок берётся случайно из Oktell за период по фильтру исход/вход, которого ещё не было
// в оценках, и сразу пишется как imported (не оценён). Супервайзер удалить его не может.
const rcDefaultRange = (mo) => {
    const start = rcMonthStartKey(mo);
    const today = rcToKey(new Date());
    let end = rcMonthEndKey(mo);
    if (end > today) end = today;       // не уводим конец периода в будущее (текущий месяц)
    if (end < start) end = start;
    return { start, end };
};

const RC_TEZ_MAX_DAYS = 7;       // TEZ: период Binotel ограничен 7 днями (лимит частоты API)
const RC_MAX_COUNT = 20;         // максимум звонков за один запрос (совпадает с бэкендом)

const RandomCallModal = ({ isOpen, onClose, operator, userId, selectedMonth, source, onImported }) => {
    const isBinotel = source === 'binotel'; // TEZ: min/max длительности задаются в модалке
    const todayKey = rcToKey(new Date());
    // TEZ: выбор ограничен ВЫБРАННЫМ месяцем и максимум 7 днями (иначе Binotel упирается
    // в rate limit) — даты только внутри месяца селектора и не позже сегодня.
    const monthStartKey = rcMonthStartKey(selectedMonth);
    const monthEndKey = rcMonthEndKey(selectedMonth);
    const binotelMinKey = isBinotel ? monthStartKey : null;
    const binotelMaxKey = isBinotel ? (monthEndKey < todayKey ? monthEndKey : todayKey) : null;
    const rcInitialRange = () => {
        if (!isBinotel) return rcDefaultRange(selectedMonth);
        const end = binotelMaxKey;
        const endDate = rcParseKey(end);
        let start = endDate ? rcToKey(rcAddDays(endDate, -(RC_TEZ_MAX_DAYS - 1))) : monthStartKey;
        if (start < monthStartKey) start = monthStartKey;   // не выходим за начало месяца
        return { start, end };
    };
    const [range, setRange] = useState(rcInitialRange);
    const [incoming, setIncoming] = useState(true);
    const [outgoing, setOutgoing] = useState(true);
    const [minDur, setMinDur] = useState('');
    const [maxDur, setMaxDur] = useState('');
    const [count, setCount] = useState(1);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [results, setResults] = useState([]);
    const importedMonthsRef = useRef(new Set());

    useEffect(() => {
        if (!isOpen) return;
        setRange(rcInitialRange());
        setIncoming(true); setOutgoing(true); setMinDur(''); setMaxDur(''); setCount(1);
        setBusy(false); setError(''); setResults([]);
        importedMonthsRef.current = new Set();
    }, [isOpen, selectedMonth]);

    if (!isOpen) return null;

    const parseDur = (v) => {
        const n = parseInt(String(v).trim(), 10);
        return Number.isFinite(n) && n >= 0 ? n : null;
    };
    const minDurNum = parseDur(minDur);
    const maxDurNum = parseDur(maxDur);
    const durRangeInvalid = isBinotel && minDurNum != null && maxDurNum != null && maxDurNum < minDurNum;
    const countNum = Math.max(1, Math.min(parseInt(String(count), 10) || 1, RC_MAX_COUNT));
    const spanTooLong = isBinotel && rcSpanDays(range.start, range.end) > RC_TEZ_MAX_DAYS;

    const fetchOne = async () => {
        if (!operator || busy || durRangeInvalid || spanTooLong) return;
        setBusy(true); setError('');
        try {
            const body = { operator_id: operator.id, date_from: range.start, date_to: range.end, incoming, outgoing, count: countNum };
            if (isBinotel) {
                if (minDurNum != null) body.min_duration_sec = minDurNum;
                if (maxDurNum != null) body.max_duration_sec = maxDurNum;
            }
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluations/random_call`, {
                method: 'POST',
                headers: { 'X-User-Id': userId, 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const d = await r.json().catch(() => ({}));
            const list = Array.isArray(d.calls) ? d.calls : (d.call ? [d.call] : []);
            if (r.ok && d.status === 'success' && list.length > 0) {
                setResults(prev => [...list, ...prev]);
                list.forEach(c => { if (c.month) importedMonthsRef.current.add(c.month); });
                const n = list.length;
                const word = n === 1 ? 'звонок' : (n >= 2 && n <= 4 ? 'звонка' : 'звонков');
                emitCallEvaluationToast(`Добавлено в журнал: ${n} ${word}`, 'success');
            } else {
                setError(d.error || 'Не удалось получить звонок');
            }
        } catch (e) {
            setError('Сетевая ошибка, попробуйте ещё раз');
        } finally {
            setBusy(false);
        }
    };

    const handleClose = () => {
        const months = importedMonthsRef.current;
        if (months.size > 0 && typeof onImported === 'function') {
            onImported(months, results[0]?.month || null);
        }
        onClose?.();
    };

    const TypeCheck = ({ checked, onToggle, label }) => (
        <button type="button" onClick={onToggle}
            style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', borderRadius: 'var(--radius)', cursor: 'pointer',
                border: `1px solid ${checked ? 'var(--accent)' : 'var(--border-strong)'}`,
                background: checked ? 'var(--accent-light)' : 'var(--surface)' }}>
            <span style={{ width: 18, height: 18, flexShrink: 0, borderRadius: 5, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: '#fff',
                background: checked ? 'var(--accent)' : 'transparent', border: `1px solid ${checked ? 'var(--accent)' : 'var(--border-strong)'}` }}>
                {checked ? <FaIcon className="fas fa-check" /> : null}
            </span>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{label}</span>
        </button>
    );

    const span = rcSpanDays(range.start, range.end);

    return (
        <div className="modal-backdrop" onClick={handleClose}>
            <div className="modal" style={{ maxWidth: 460 }} onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <div>
                        <h2><FaIcon className="fas fa-shuffle" /> Случайный звонок</h2>
                        <div className="modal-header-sub">{operator?.name || '—'}</div>
                    </div>
                    <button className="close-btn" onClick={handleClose}><FaIcon className="fas fa-times" /></button>
                </div>
                <div className="modal-body">
                    <label className="label" style={{ marginBottom: 6, display: 'block' }}>
                        Период · {span} дн.{isBinotel ? ` (не более ${RC_TEZ_MAX_DAYS}, в пределах месяца)` : ''}
                    </label>
                    <RcRangeCalendar value={range} onChange={setRange} maxKey={binotelMaxKey || todayKey} minKey={binotelMinKey} />
                    {spanTooLong ? (
                        <div style={{ marginTop: 6, fontSize: 12, color: 'var(--red)' }}>
                            Для TEZ период не больше {RC_TEZ_MAX_DAYS} дней — сузьте выбор.
                        </div>
                    ) : null}

                    <label className="label" style={{ margin: '16px 0 6px', display: 'block' }}>Тип звонка</label>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <TypeCheck checked={outgoing} label="Исходящие" onToggle={() => { if (outgoing && !incoming) return; setOutgoing(v => !v); }} />
                        <TypeCheck checked={incoming} label="Входящие" onToggle={() => { if (incoming && !outgoing) return; setIncoming(v => !v); }} />
                    </div>

                    <label className="label" style={{ margin: '16px 0 6px', display: 'block' }}>Сколько звонков (1–{RC_MAX_COUNT})</label>
                    <input type="number" min="1" max={RC_MAX_COUNT} inputMode="numeric" value={count}
                        onChange={e => setCount(e.target.value)}
                        onBlur={() => setCount(countNum)}
                        style={{ width: '100%', padding: '9px 12px', borderRadius: 'var(--radius)', border: '1px solid var(--border-strong)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />

                    {isBinotel ? (
                        <>
                            <label className="label" style={{ margin: '16px 0 6px', display: 'block' }}>Длительность разговора, сек (необязательно)</label>
                            <div style={{ display: 'flex', gap: 8 }}>
                                <input type="number" min="0" inputMode="numeric" placeholder="мин" value={minDur}
                                    onChange={e => setMinDur(e.target.value)}
                                    style={{ flex: 1, padding: '9px 12px', borderRadius: 'var(--radius)', border: '1px solid var(--border-strong)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
                                <input type="number" min="0" inputMode="numeric" placeholder="макс" value={maxDur}
                                    onChange={e => setMaxDur(e.target.value)}
                                    style={{ flex: 1, padding: '9px 12px', borderRadius: 'var(--radius)', border: '1px solid var(--border-strong)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
                            </div>
                            {durRangeInvalid ? (
                                <div style={{ marginTop: 6, fontSize: 12, color: 'var(--red)' }}>Макс. длительность должна быть не меньше мин.</div>
                            ) : null}
                        </>
                    ) : null}

                    {error ? (
                        <div style={{ marginTop: 14, padding: '10px 12px', borderRadius: 'var(--radius)', background: 'var(--accent-light)', border: '1px solid var(--border)', fontSize: 13, color: 'var(--red)' }}>
                            <FaIcon className="fas fa-circle-exclamation" /> {error}
                        </div>
                    ) : null}

                    {results.length > 0 ? (
                        <div style={{ marginTop: 16 }}>
                            <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 8 }}>
                                Добавлено в журнал ({results.length}) · не оценён, удалить может только администратор:
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 200, overflowY: 'auto' }}>
                                {results.map(c => (
                                    <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', borderRadius: 'var(--radius)', border: '1px solid var(--border)', background: 'var(--surface-2)' }}>
                                        <span className={`badge ${c.direction === 'in' ? 'badge-blue' : 'badge-green'}`}>
                                            <span className="badge-dot" />{c.direction === 'in' ? 'Входящий' : 'Исходящий'}
                                        </span>
                                        <div style={{ minWidth: 0, flex: 1 }}>
                                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', fontFamily: 'var(--font-mono)' }}>{c.phone || '—'}</div>
                                            <div style={{ fontSize: 11, color: 'var(--text-3)' }}>{c.datetime || '—'}{c.duration_sec != null ? ` · ${Math.round(c.duration_sec)} сек` : ''}</div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    ) : null}
                </div>
                <div className="modal-footer">
                    <button className="btn btn-secondary" onClick={handleClose}>Закрыть</button>
                    <button className="btn btn-primary" onClick={fetchOne} disabled={busy || durRangeInvalid || spanTooLong}>
                        {busy ? <><span className="spinner" /> Поиск…</> : <><FaIcon className="fas fa-shuffle" /> {results.length > 0 ? 'Получить ещё' : (countNum > 1 ? `Получить звонки (${countNum})` : 'Получить случайный звонок')}</>}
                    </button>
                </div>
            </div>
        </div>
    );
};

// ─── Оценка чатов: Chat2Desk (ЧМ СЗоВ) и Wazzup (Верификаторы ОП) ───────────
// «Случайный чат» вместо «Случайного звонка»: кандидат выбирается по фильтрам
// (заявка c2d_requests либо эпизод wazzup_episodes — источник задаёт
// random_chat_source направления), переписка снапшотится, оценка идёт обычной
// записью журнала с критериями направления + цитаты, выделяемые из текста.

const chatSquash = (text) => String(text || '').split(/\s+/).join(' ').trim().toLowerCase();

// Ссылка на сам чат в веб-приложении Wazzup (там доступна и история старше 45
// дней) — тот же формат, что в разделе «Чаты Верификаторов» (WazzupChatsView).
// Ключи берём из wazzup-снапшота: transport = chatType, wz_chat_id, wz_channel_id.
const WAZZUP_APP_BASE = 'https://app.wazzup24.com/6757-7677';
const wazzupChatUrlFromSnapshot = (s) => (
    s && s.source === 'wazzup' && s.wz_chat_id && s.wz_channel_id
        ? `${WAZZUP_APP_BASE}/chat/${s.transport || 'whatsapp'}/${encodeURIComponent(s.wz_chat_id)}/${s.wz_channel_id}`
        : null
);

const chatFmtTime = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};
const chatFmtDay = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
};

// Подсветка цитат: точное вхождение без учёта регистра.
const chatHighlight = (text, quoteTexts) => {
    if (!text || !quoteTexts?.length) return text;
    let segments = [{ text, mark: false }];
    quoteTexts.forEach((quote) => {
        const q = String(quote || '');
        if (!q) return;
        const next = [];
        segments.forEach((seg) => {
            if (seg.mark) { next.push(seg); return; }
            const idx = seg.text.toLowerCase().indexOf(q.toLowerCase());
            if (idx === -1) { next.push(seg); return; }
            if (idx > 0) next.push({ text: seg.text.slice(0, idx), mark: false });
            next.push({ text: seg.text.slice(idx, idx + q.length), mark: true });
            if (idx + q.length < seg.text.length) next.push({ text: seg.text.slice(idx + q.length), mark: false });
        });
        segments = next;
    });
    return segments.map((seg, i) => seg.mark
        ? <mark key={i} style={{ background: '#fde68a', borderRadius: 3, padding: '0 1px' }}>{seg.text}</mark>
        : <React.Fragment key={i}>{seg.text}</React.Fragment>);
};

/* Лайтбокс фото — как в «Чатах Верификаторов»: просмотр поверх окна,
 * оригинал открывается отдельной кнопкой, а не прыжком по ссылке. */
const ChatImageLightbox = ({ url, onClose }) => {
    useEffect(() => {
        const onKeyDown = (e) => {
            if (e.key !== 'Escape') return;
            // capture + stopImmediatePropagation: Esc закрывает только фото и не
            // доходит до Esc-обработчика полноэкранного окна оценки/модалок.
            e.stopImmediatePropagation();
            e.preventDefault();
            onClose();
        };
        window.addEventListener('keydown', onKeyDown, true);
        return () => window.removeEventListener('keydown', onKeyDown, true);
    }, [onClose]);
    return (
        <div onClick={onClose}
             style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(15,23,42,0.78)',
                      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                      padding: 24, cursor: 'zoom-out' }}>
            <img src={url} alt="" onClick={e => e.stopPropagation()}
                 style={{ maxWidth: '92vw', maxHeight: '80vh', borderRadius: 12, cursor: 'default',
                          boxShadow: '0 16px 56px rgba(0,0,0,0.45)', background: '#fff' }} />
            <div onClick={e => e.stopPropagation()} style={{ marginTop: 14, display: 'flex', gap: 10 }}>
                <a className="btn btn-secondary btn-sm" href={url} target="_blank" rel="noopener noreferrer">
                    <FaIcon className="fas fa-up-right-from-square" /> Открыть оригинал
                </a>
                <button className="btn btn-primary btn-sm" onClick={onClose}>
                    <FaIcon className="fas fa-times" /> Закрыть
                </button>
            </div>
        </div>
    );
};

const ChatMedia = ({ msg }) => {
    const [zoom, setZoom] = useState(null); // url фото в лайтбоксе
    const chip = {
        display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 8px',
        borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)',
        fontSize: 12, color: 'var(--text-2)', textDecoration: 'none',
    };
    const pieces = [];
    if (msg.photo) {
        pieces.push(
            <img key="photo" src={msg.photo} alt="" loading="lazy"
                 onClick={() => setZoom(msg.photo)}
                 style={{ maxHeight: 220, maxWidth: '100%', borderRadius: 10, cursor: 'zoom-in', display: 'block' }} />
        );
    }
    if (msg.video) pieces.push(<video key="video" controls preload="metadata" src={msg.video} style={{ maxHeight: 220, maxWidth: '100%', borderRadius: 10, display: 'block' }} />);
    if (msg.audio) pieces.push(<audio key="audio" controls preload="none" src={msg.audio} style={{ width: 230, maxWidth: '100%', height: 36 }} />);
    if (msg.pdf) pieces.push(<a key="pdf" href={msg.pdf} target="_blank" rel="noopener noreferrer" style={chip}><FaIcon className="fas fa-file-pdf" /> PDF</a>);
    (msg.attachments || []).forEach((att, i) => {
        if (msg.photo && att.link === msg.photo) return;
        pieces.push(<a key={`att-${i}`} href={att.link} target="_blank" rel="noopener noreferrer" style={chip}><FaIcon className="fas fa-paperclip" /> {att.name || 'Файл'}</a>);
    });
    if (!pieces.length) return null;
    return (
        <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>{pieces}</div>
            {zoom && <ChatImageLightbox url={zoom} onClose={() => setZoom(null)} />}
        </>
    );
};

/* Лента переписки заявки. selectable — включает «Цитировать» по выделению текста
 * (data-mid на пузыре указывает сообщение). quotes подсвечиваются жёлтым. */
const ChatThread = ({ snapshot, quotes = [], selectable = false, onAddQuote, height = 420 }) => {
    const boxRef = useRef(null);
    const [hideService, setHideService] = useState(false);
    const [selection, setSelection] = useState(null);

    const quotesByMessage = {};
    (quotes || []).forEach((q) => {
        const key = String(q.messageId);
        (quotesByMessage[key] = quotesByMessage[key] || []).push(q.text);
    });

    const allMessages = snapshot?.messages || [];
    const messages = hideService
        ? allMessages.filter((m) => m.type !== 'system' && m.type !== 'autoreply')
        : allMessages;

    const handleMouseUp = () => {
        if (!selectable) return;
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed) { setSelection(null); return; }
        const text = sel.toString().trim();
        if (!text) { setSelection(null); return; }
        const toEl = (node) => (node && node.nodeType === 3 ? node.parentElement : node);
        const a = toEl(sel.anchorNode)?.closest?.('[data-mid]');
        const b = toEl(sel.focusNode)?.closest?.('[data-mid]');
        if (!a || a !== b) { setSelection(null); return; }
        const rect = sel.getRangeAt(0).getBoundingClientRect();
        const box = boxRef.current?.getBoundingClientRect();
        if (!box) return;
        setSelection({
            messageId: a.dataset.mid, text,
            x: Math.min(Math.max(rect.left + rect.width / 2 - box.left, 70), box.width - 70),
            y: rect.top - box.top + (boxRef.current?.scrollTop || 0),
        });
    };

    const addQuote = () => {
        if (!selection) return;
        onAddQuote?.(selection);
        setSelection(null);
        window.getSelection()?.removeAllRanges();
    };

    let lastDay = null;
    return (
        <div style={{ display: 'flex', flexDirection: 'column', height, minHeight: 260, border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden', background: 'var(--surface-2)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '8px 12px', background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
                <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {snapshot?.client_name || snapshot?.client_phone || 'Клиент'}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {snapshot?.client_phone ? `${snapshot.client_phone} · ` : ''}{snapshot?.channel_name || ''}{snapshot?.transport ? ` · ${snapshot.transport}` : ''} · {snapshot?.messages_count ?? 0} сообщ.
                    </div>
                </div>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setHideService(v => !v)}>
                    <FaIcon className={`fas fa-${hideService ? 'eye' : 'eye-slash'}`} /> {hideService ? 'Автоответы' : 'Без автоответов'}
                </button>
            </div>
            <div ref={boxRef} onMouseUp={handleMouseUp} style={{ position: 'relative', flex: 1, overflowY: 'auto', overscrollBehavior: 'contain', padding: '10px 12px' }}>
                {/* Колонка сообщений ограничена по ширине: на широком экране строки
                    по 1200px нечитаемы и цитату неудобно выделять. */}
                <div style={{ width: '100%', maxWidth: 960, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {messages.length === 0 && (
                    <div style={{ textAlign: 'center', color: 'var(--text-3)', fontSize: 13, padding: '30px 0' }}>Сообщений нет</div>
                )}
                {messages.map((m) => {
                    const day = (m.created || '').slice(0, 10);
                    const daySep = day && day !== lastDay
                        ? <div key={`day-${day}`} style={{ textAlign: 'center', margin: '6px 0 2px' }}>
                              <span style={{ fontSize: 11, color: 'var(--text-3)', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 999, padding: '2px 10px' }}>{chatFmtDay(m.created)}</span>
                          </div>
                        : null;
                    lastDay = day || lastDay;
                    if (m.type === 'system') {
                        return (
                            <React.Fragment key={m.id}>
                                {daySep}
                                <div style={{ textAlign: 'center' }}>
                                    <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{m.text || 'Системное сообщение'} · {chatFmtTime(m.created)}</span>
                                </div>
                            </React.Fragment>
                        );
                    }
                    const out = m.type === 'to_client';
                    const auto = m.type === 'autoreply';
                    // Внутренний комментарий оператора (Chat2Desk type='comment'):
                    // клиент его не видит, поэтому это НЕ его реплика — рисуем на
                    // стороне оператора отдельным стилем, иначе заметка вроде
                    // «нет ответа/обед» читается как сообщение клиента.
                    const note = m.type === 'comment';
                    const bubbleStyle = {
                        maxWidth: 'min(78%, 640px)', padding: '7px 10px', borderRadius: 12, fontSize: 13, lineHeight: 1.45,
                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                        ...(out
                            ? { background: 'var(--accent)', color: '#fff', borderBottomRightRadius: 4 }
                            : note
                                ? { background: '#fffbeb', color: '#78350f', border: '1px dashed #fcd34d', borderBottomRightRadius: 4 }
                                : auto
                                    ? { background: 'var(--surface)', color: 'var(--text-3)', border: '1px dashed var(--border-strong)', borderBottomRightRadius: 4 }
                                    : { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)', borderBottomLeftRadius: 4 }),
                    };
                    const hasMedia = Boolean(m.photo || m.video || m.audio || m.pdf || (m.attachments || []).length);
                    return (
                        <React.Fragment key={m.id}>
                            {daySep}
                            <div style={{ display: 'flex', justifyContent: (out || auto || note) ? 'flex-end' : 'flex-start' }}>
                                <div data-mid={m.id} style={bubbleStyle}>
                                    {auto && <div style={{ fontSize: 10.5, fontWeight: 600, marginBottom: 2, display: 'flex', alignItems: 'center', gap: 4 }}><FaIcon className="fas fa-robot" /> Автоответ</div>}
                                    {note && <div style={{ fontSize: 10.5, fontWeight: 600, marginBottom: 2, display: 'flex', alignItems: 'center', gap: 4, color: '#b45309' }}><FaIcon className="fas fa-lock" /> Внутренний комментарий{m.author ? ` · ${m.author}` : ''}</div>}
                                    {/* Автор исходящего: у эпизода Wazzup их может быть
                                        несколько, поэтому подпись у каждого сообщения своя. */}
                                    {!auto && !note && m.author && (
                                        <div style={{ fontSize: 10.5, fontWeight: 600, marginBottom: 2, opacity: 0.85, display: 'flex', alignItems: 'center', gap: 4 }}>
                                            <FaIcon className="fas fa-headset" /> {m.author}
                                        </div>
                                    )}
                                    {hasMedia && <div style={{ marginBottom: m.text ? 5 : 0 }}><ChatMedia msg={m} /></div>}
                                    {m.text ? chatHighlight(m.text, quotesByMessage[String(m.id)]) : (!hasMedia && <em style={{ opacity: 0.7 }}>[сообщение]</em>)}
                                    <div style={{ fontSize: 10, opacity: 0.65, textAlign: 'right', marginTop: 3 }}>{chatFmtTime(m.created)}</div>
                                </div>
                            </div>
                        </React.Fragment>
                    );
                })}
                </div>
                {selectable && selection && (
                    <button type="button" onClick={addQuote}
                        style={{ position: 'absolute', left: selection.x, top: Math.max(selection.y - 36, 4), transform: 'translateX(-50%)',
                                 zIndex: 5, display: 'inline-flex', alignItems: 'center', gap: 6,
                                 background: 'var(--text)', color: 'var(--surface)', border: 'none', borderRadius: 999,
                                 padding: '5px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer', boxShadow: '0 4px 12px rgba(0,0,0,0.25)' }}>
                        <FaIcon className="fas fa-quote-left" /> Цитировать
                    </button>
                )}
            </div>
        </div>
    );
};

/* Источники «Случайного чата». Отличаются эндпоинтом пика и тем, есть ли у
 * канала оценка клиента: CSAT собирают только в Chat2Desk (у Wazzup его нет,
 * у ChatApp /v1/feedbacks пуст), поэтому у остальных фильтр скрыт. */
const CHAT_SOURCE_META = {
    chat2desk: { endpoint: 'c2d_eval', label: 'Chat2Desk', retention: 'заявки хранятся 45 дней', hasRating: true },
    wazzup: { endpoint: 'wz_eval', label: 'WhatsApp (Wazzup)', retention: 'переписка хранится 45 дней', hasRating: false },
    chatapp: { endpoint: 'ca_eval', label: 'WhatsApp (ChatApp)', retention: 'переписка хранится 45 дней', hasRating: false },
};

/* Модалка «Случайный чат»: настройки выборки (период в пределах месяца, длина
 * чата, оценка клиента, без уже оценённых) -> POST /api/<endpoint>/pick. */
const RandomChatModal = ({ isOpen, onClose, operator, userId, selectedMonth, onPicked, source = 'chat2desk' }) => {
    const sourceMeta = CHAT_SOURCE_META[source] || CHAT_SOURCE_META.chat2desk;
    const todayKey = rcToKey(new Date());
    const monthStartKey = rcMonthStartKey(selectedMonth);
    const monthEndKey = rcMonthEndKey(selectedMonth);
    const maxKey = monthEndKey < todayKey ? monthEndKey : todayKey;
    const initialRange = () => ({ start: monthStartKey, end: maxKey });
    const [range, setRange] = useState(initialRange);
    const [minMsgs, setMinMsgs] = useState(4);
    const [maxMsgs, setMaxMsgs] = useState('');
    const [ratingFilter, setRatingFilter] = useState('');
    const [excludeEvaluated, setExcludeEvaluated] = useState(true);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!isOpen) return;
        setRange(initialRange());
        setMinMsgs(4); setMaxMsgs(''); setRatingFilter(''); setExcludeEvaluated(true);
        setBusy(false); setError('');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen, selectedMonth]);

    if (!isOpen) return null;

    const fetchChat = async () => {
        if (!operator || busy) return;
        setBusy(true); setError('');
        try {
            const body = {
                operator_id: operator.id,
                date_from: range.start,
                date_to: range.end,
                min_messages: parseInt(minMsgs, 10) || 1,
                exclude: excludeEvaluated ? 'any' : 'none',
            };
            const maxNum = parseInt(maxMsgs, 10);
            if (Number.isFinite(maxNum) && maxNum > 0) body.max_messages = maxNum;
            if (sourceMeta.hasRating && ratingFilter) body.rating_filter = ratingFilter;
            const r = await authFetch(`${API_BASE_URL}/api/${sourceMeta.endpoint}/pick`, {
                method: 'POST',
                headers: { 'X-User-Id': userId, 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const d = await r.json().catch(() => ({}));
            if (r.ok && d.status === 'success' && d.snapshot) {
                onPicked?.(d);
                onClose?.();
            } else if (d.status === 'empty') {
                setError(d.message || 'По заданным фильтрам чатов не нашлось');
            } else {
                setError(d.error || 'Не удалось получить чат');
            }
        } catch (e) {
            setError('Сетевая ошибка, попробуйте ещё раз');
        } finally {
            setBusy(false);
        }
    };

    const inputStyle = { width: '100%', padding: '9px 12px', borderRadius: 'var(--radius)', border: '1px solid var(--border-strong)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 };

    return (
        <div className="modal-backdrop" onClick={onClose}>
            <div className="modal" style={{ maxWidth: 460 }} onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <div>
                        <h2><FaIcon className="fas fa-shuffle" /> Случайный чат</h2>
                        <div className="modal-header-sub">{operator?.name || '—'} · {sourceMeta.label}</div>
                    </div>
                    <button className="close-btn" onClick={onClose}><FaIcon className="fas fa-times" /></button>
                </div>
                <div className="modal-body">
                    <label className="label" style={{ marginBottom: 6, display: 'block' }}>
                        Период (в пределах месяца; {sourceMeta.retention})
                    </label>
                    <RcRangeCalendar value={range} onChange={setRange} maxKey={maxKey} minKey={monthStartKey} />

                    <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                        <div style={{ flex: 1 }}>
                            <label className="label" style={{ marginBottom: 6, display: 'block' }}>Мин. сообщений</label>
                            <input type="number" min="1" inputMode="numeric" value={minMsgs} onChange={e => setMinMsgs(e.target.value)} style={inputStyle} />
                        </div>
                        <div style={{ flex: 1 }}>
                            <label className="label" style={{ marginBottom: 6, display: 'block' }}>Макс. (необязательно)</label>
                            <input type="number" min="1" inputMode="numeric" placeholder="Без лимита" value={maxMsgs} onChange={e => setMaxMsgs(e.target.value)} style={inputStyle} />
                        </div>
                    </div>

                    {sourceMeta.hasRating && (
                        <>
                            <label className="label" style={{ margin: '16px 0 6px', display: 'block' }}>Оценка клиента</label>
                            <select className="select" value={ratingFilter} onChange={e => setRatingFilter(e.target.value)}>
                                <option value="">Не важно</option>
                                <option value="rated">С оценкой клиента</option>
                                <option value="unrated">Без оценки клиента</option>
                                <option value="low">Низкая оценка (&lt; 4)</option>
                            </select>
                        </>
                    )}

                    <button type="button" onClick={() => setExcludeEvaluated(v => !v)}
                        style={{ marginTop: 16, width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', borderRadius: 'var(--radius)', cursor: 'pointer',
                                 border: `1px solid ${excludeEvaluated ? 'var(--accent)' : 'var(--border-strong)'}`,
                                 background: excludeEvaluated ? 'var(--accent-light)' : 'var(--surface)' }}>
                        <span style={{ width: 18, height: 18, flexShrink: 0, borderRadius: 5, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: '#fff',
                                       background: excludeEvaluated ? 'var(--accent)' : 'transparent', border: `1px solid ${excludeEvaluated ? 'var(--accent)' : 'var(--border-strong)'}` }}>
                            {excludeEvaluated ? <FaIcon className="fas fa-check" /> : null}
                        </span>
                        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>Не предлагать уже оценённые чаты</span>
                    </button>

                    {error ? (
                        <div style={{ marginTop: 14, padding: '10px 12px', borderRadius: 'var(--radius)', background: 'var(--red-light)', border: '1px solid #fca5a5', fontSize: 13, color: 'var(--red)' }}>
                            <FaIcon className="fas fa-circle-exclamation" /> {error}
                        </div>
                    ) : null}
                </div>
                <div className="modal-footer">
                    <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                    <button className="btn btn-primary" onClick={fetchChat} disabled={busy}>
                        {busy ? <><span className="spinner" /> Поиск…</> : <><FaIcon className="fas fa-shuffle" /> Получить случайный чат</>}
                    </button>
                </div>
            </div>
        </div>
    );
};

/* Оценка чата: слева переписка (цитаты выделением), справа критерии направления
 * ЧМ (та же механика и формула, что у звонков) + цитаты с комментариями.
 * Сабмит — обычный POST /api/call_evaluation с c2d_snapshot_id/chat_quotes. */
const ChatEvaluationModal = ({ isOpen, onClose, operator, chatData, directions, selectedMonth, userId, userName, onSubmitted }) => {
    const snapshot = chatData?.snapshot || null;
    const request = chatData?.request || null;
    // Обычно критерии берутся у направления самого оператора. Исключение — ТЭЗ:
    // техменеджеры числятся на «ТП линия», а чат оценивается по критериям «ТП
    // чат». Подмену считает бэкенд (random_chat_criteria_direction_id), сюда
    // приходит уже готовый id; оценка ляжет в журнал с ним же, и критерии в
    // журнале подтянутся правильные — они join-ятся от calls.direction_id.
    const operatorDirection = (directions || []).find(d => Number(d.id) === Number(operator?.direction_id)) || null;
    const criteriaDirectionId = operatorDirection?.random_chat_criteria_direction_id ?? operator?.direction_id;
    const direction = (directions || []).find(d => Number(d.id) === Number(criteriaDirectionId)) || operatorDirection;
    const criteria = direction?.criteria || [];

    const [scores, setScores] = useState([]);
    const [comments, setComments] = useState([]);
    const [commentVisible, setCommentVisible] = useState([]);
    const [generalComment, setGeneralComment] = useState('');
    const [commentVisibleToOperator, setCommentVisibleToOperator] = useState(true);
    const [quotes, setQuotes] = useState([]);
    const [infoIndex, setInfoIndex] = useState(null);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [showPanel, setShowPanel] = useState(true); // панель оценки справа

    // Несохранённые изменения: любые тронутые критерии, комментарии или цитаты.
    const isDirty = quotes.length > 0
        || String(generalComment || '').trim() !== ''
        || scores.some((s) => s !== 'Correct')
        || comments.some((c) => String(c || '').trim() !== '');

    const closeGuarded = () => {
        if (isSubmitting) return;
        if (isDirty && !window.confirm('Закрыть окно без сохранения оценки? Выставленные критерии и цитаты будут потеряны.')) return;
        onClose?.();
    };

    useEffect(() => {
        if (!isOpen) return;
        setScores(criteria.map(() => 'Correct'));
        setComments(criteria.map(() => ''));
        setCommentVisible(criteria.map(() => false));
        setGeneralComment('');
        setCommentVisibleToOperator(true);
        setQuotes([]);
        setInfoIndex(null);
        setIsSubmitting(false);
        setShowPanel(true);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen, snapshot?.id]);

    // Полноэкранное окно: блокируем прокрутку журнала под собой + Esc для закрытия.
    useEffect(() => {
        if (!isOpen) return;
        const prevOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        const onKeyDown = (e) => {
            if (e.key === 'Escape') {
                e.stopPropagation();
                closeGuarded();
            }
        };
        window.addEventListener('keydown', onKeyDown);
        return () => {
            document.body.style.overflow = prevOverflow;
            window.removeEventListener('keydown', onKeyDown);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen, isDirty, isSubmitting]);

    if (!isOpen || !snapshot) return null;

    const wazzupUrl = wazzupChatUrlFromSnapshot(snapshot);
    const hasCriticalError = criteria.some((c, i) => c.isCritical && scores[i] === 'Error');
    const totalScore = hasCriticalError ? 0 : criteria.reduce((sum, c, i) => {
        if (c.isCritical) return sum;
        if (scores[i] === 'Correct' || scores[i] === 'N/A') return sum + c.weight;
        if (scores[i] === 'Deficiency' && c.deficiency) return sum + c.deficiency.weight;
        return sum;
    }, 0);

    const addQuote = ({ messageId, text }) => {
        const msg = (snapshot.messages || []).find((m) => String(m.id) === String(messageId));
        if (!msg || !chatSquash(msg.text).includes(chatSquash(text))) {
            emitCallEvaluationToast('Выделите фрагмент внутри одного сообщения', 'error');
            return;
        }
        setQuotes(prev => [...prev, { messageId: msg.id, text, comment: '' }]);
    };

    // Клик по цитате — проскроллить чат к её сообщению и подсветить его.
    const scrollToMessage = (messageId) => {
        const el = document.querySelector(`[data-mid="${messageId}"]`);
        if (!el) return;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('chat-msg-flash');
        setTimeout(() => el.classList.remove('chat-msg-flash'), 1400);
    };

    const isSubmitDisabled = !criteria.length ||
        scores.some((s, i) => (s === 'Error' || s === 'Incorrect') && !comments[i]?.trim());
    const submitTitle = !criteria.length
        ? 'У направления нет критериев'
        : isSubmitDisabled
            ? 'Заполните комментарии к критериям с ошибками'
            : `Сохранить оценку ${totalScore}/100 в журнал`;

    const handleSubmit = async () => {
        if (isSubmitDisabled || isSubmitting) return;
        // Защита от «сохранил не глядя»: все критерии в дефолтном «Корректно»
        // и ничего не заполнено — просим явное подтверждение сотки.
        if (!isDirty && !window.confirm('Все критерии остались «Корректно» — сохранить оценку 100/100?')) return;
        setIsSubmitting(true);
        try {
            const fd = new FormData();
            fd.append('evaluator', userName);
            fd.append('operator', operator?.name || '');
            fd.append('phone_number', snapshot.client_phone || snapshot.client_name || `chat_${snapshot.request_id ?? snapshot.wz_chat_id ?? snapshot.id}`);
            fd.append('appeal_date', request?.request_start || `${snapshot.day}T00:00:00`);
            fd.append('score', totalScore);
            fd.append('comment', String(generalComment || '').trim());
            fd.append('comment_visible_to_operator', String(!!commentVisibleToOperator));
            fd.append('month', selectedMonth);
            fd.append('is_draft', 'false');
            fd.append('scores', JSON.stringify(scores));
            fd.append('criterion_comments', JSON.stringify(comments));
            fd.append('direction', String(direction?.id ?? operator?.direction_id ?? ''));
            fd.append('c2d_snapshot_id', String(snapshot.id));
            fd.append('chat_quotes', JSON.stringify(quotes));
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation`, { method: 'POST', headers: { 'X-User-Id': userId }, body: fd });
            const res = await r.json().catch(() => ({}));
            if (r.ok && res.status === 'success') {
                emitCallEvaluationToast('Оценка чата сохранена в журнал', 'success');
                onSubmitted?.();
                onClose?.();
            } else {
                emitCallEvaluationToast('Ошибка: ' + (res.error || 'не удалось сохранить'), 'error');
            }
        } catch (e) {
            emitCallEvaluationToast('Ошибка отправки: ' + e.message, 'error');
        } finally {
            setIsSubmitting(false);
        }
    };

    // Полноэкранное окно (z-index 150: выше слоёв журнала и .modal-backdrop=100,
    // ниже .info-panel=200 и тостов). Чат — на весь экран, цитаты — сразу под
    // чатом (где их создают), критерии — панель справа, сворачивается кнопкой.
    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 150, display: 'flex', flexDirection: 'column', background: 'var(--surface-2)' }}>
            {/* Шапка: одна строка, заголовок усечётся, контролы не переносятся */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        <FaIcon className="fas fa-comments" style={{ flexShrink: 0 }} /> Оценка чата · {operator?.name || '—'}
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {snapshot.source === 'wazzup' ? 'Эпизод WhatsApp' : `Заявка #${snapshot.request_id}`} · {request?.day || snapshot.day || ''}
                        {request?.rating_score != null ? ` · оценка клиента: ${request.rating_score}` : ''}
                        {' · '}{direction?.name || 'направление не найдено'}
                    </div>
                </div>
                <span className={`badge ${hasCriticalError ? 'badge-red' : totalScore >= 80 ? 'badge-green' : 'badge-blue'}`}
                      style={{ fontSize: 13, flexShrink: 0 }}
                      title={hasCriticalError ? 'Критическая ошибка обнуляет итог' : 'Текущий итог по критериям'}>
                    <span className="badge-dot" /> {totalScore} / 100
                </span>
                {wazzupUrl && (
                    <a className="btn btn-secondary btn-sm" style={{ flexShrink: 0 }}
                       href={wazzupUrl} target="_blank" rel="noopener noreferrer"
                       title="Открыть этот чат в Wazzup (там доступна и история старше 45 дней)">
                        <FaIcon className="fas fa-up-right-from-square" /> В Wazzup
                    </a>
                )}
                <button className="btn btn-secondary btn-sm" style={{ flexShrink: 0 }} onClick={() => setShowPanel(v => !v)}>
                    <FaIcon className={`fas fa-${showPanel ? 'chevron-right' : 'clipboard-check'}`} /> {showPanel ? 'Свернуть панель' : `Панель оценки (${criteria.length})`}
                </button>
                <button className="btn btn-secondary btn-sm" style={{ flexShrink: 0 }} onClick={closeGuarded} disabled={isSubmitting}>
                    <FaIcon className="fas fa-times" /> Закрыть
                </button>
                <button className="btn btn-primary btn-sm" style={{ flexShrink: 0 }} onClick={handleSubmit}
                        disabled={isSubmitDisabled || isSubmitting} title={submitTitle}>
                    {isSubmitting ? <><span className="spinner" /> Сохранение…</> : <><FaIcon className="fas fa-check" /> Сохранить</>}
                </button>
            </div>

            {/* Тело: слева чат + цитаты под ним, справа — панель критериев */}
            <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
                <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', padding: 10, gap: 8 }}>
                    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
                        <ChatThread snapshot={snapshot} quotes={quotes} selectable onAddQuote={addQuote} height="100%" />
                    </div>
                    {/* Цитаты живут под чатом — комментарий пишется не отходя от переписки */}
                    <div style={{ flexShrink: 0, maxHeight: '30vh', overflowY: 'auto', overscrollBehavior: 'contain' }}>
                        {!quotes.length ? (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-3)', padding: '7px 10px', border: '1px dashed var(--border-strong)', borderRadius: 'var(--radius)' }}>
                                <FaIcon className="fas fa-quote-left" /> Выделите текст сообщения — появится кнопка «Цитировать»; фрагмент попадёт сюда и подсветится у чат-менеджера.
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                <span className="label" style={{ marginBottom: 0 }}>Цитаты ({quotes.length}) — клик по тексту прокрутит чат к сообщению</span>
                                {quotes.map((q, i) => (
                                    <div key={i} style={{ borderLeft: '3px solid #f59e0b', background: 'var(--surface)', border: '1px solid var(--border)', borderLeftColor: '#f59e0b', borderLeftWidth: 3, borderRadius: 'var(--radius)', padding: '7px 10px' }}>
                                        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                                            <div style={{ flex: 1, fontSize: 12.5, fontStyle: 'italic', color: 'var(--text)', cursor: 'pointer' }}
                                                 title="Показать сообщение в чате"
                                                 onClick={() => scrollToMessage(q.messageId)}>
                                                «{q.text}»
                                            </div>
                                            <input value={q.comment || ''} placeholder="Комментарий к цитате…"
                                                   onChange={e => setQuotes(prev => prev.map((item, j) => j === i ? { ...item, comment: e.target.value } : item))}
                                                   style={{ flex: 1, minWidth: 160, padding: '5px 8px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--text)', fontSize: 12 }} />
                                            <button type="button" className="close-btn" style={{ width: 22, height: 22, fontSize: 11, flexShrink: 0 }}
                                                    title="Убрать цитату"
                                                    onClick={() => setQuotes(prev => prev.filter((_, j) => j !== i))}>
                                                <FaIcon className="fas fa-times" />
                                            </button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                {showPanel && (
                    <div className="chat-eval-panel" style={{ width: 'clamp(430px, 36vw, 600px)', maxWidth: '92vw', flexShrink: 0, borderLeft: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', overscrollBehavior: 'contain', padding: '10px 12px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                                <span className="label" style={{ marginBottom: 0 }}>Критерии · {direction?.name || '—'}</span>
                                <span style={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}>{criteria.length} шт.</span>
                            </div>
                            {!criteria.length ? (
                                <div style={{ padding: '14px 12px', border: '1px solid #fca5a5', background: 'var(--red-light)', borderRadius: 'var(--radius)', fontSize: 13, color: 'var(--red)' }}>
                                    У направления «{direction?.name || '—'}» нет критериев — настройте их в разделе направлений.
                                </div>
                            ) : (
                                <div>
                                    {criteria.map((criterion, i) => (
                                        <CriterionCard key={i} criterion={criterion} index={i}
                                            score={scores[i]} comment={comments[i]} commentVisible={commentVisible[i]}
                                            onScoreChange={(v) => setScores(prev => prev.map((s, j) => j === i ? v : s))}
                                            onCommentChange={(v) => setComments(prev => prev.map((c, j) => j === i ? v : c))}
                                            onToggleComment={() => setCommentVisible(prev => prev.map((c, j) => j === i ? !c : c))}
                                            onShowInfo={() => setInfoIndex(i)} />
                                    ))}
                                </div>
                            )}

                            <label className="label" style={{ margin: '10px 0 6px', display: 'block' }}>Общий комментарий</label>
                            <textarea className="textarea" style={{ marginTop: 0, minHeight: 56 }} rows={2}
                                      value={generalComment} onChange={e => setGeneralComment(e.target.value)}
                                      placeholder="Общий вывод по чату…" />
                            <button type="button" onClick={() => setCommentVisibleToOperator(v => !v)}
                                style={{ margin: '8px 0 4px', display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 'var(--radius)', cursor: 'pointer', width: '100%',
                                         border: `1px solid ${commentVisibleToOperator ? 'var(--accent)' : 'var(--border-strong)'}`,
                                         background: commentVisibleToOperator ? 'var(--accent-light)' : 'var(--surface)' }}>
                                <span style={{ width: 16, height: 16, flexShrink: 0, borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 9, color: '#fff',
                                               background: commentVisibleToOperator ? 'var(--accent)' : 'transparent', border: `1px solid ${commentVisibleToOperator ? 'var(--accent)' : 'var(--border-strong)'}` }}>
                                    {commentVisibleToOperator ? <FaIcon className="fas fa-check" /> : null}
                                </span>
                                <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>Показывать комментарий чат-менеджеру</span>
                            </button>
                        </div>
                    </div>
                )}
            </div>

            {infoIndex !== null && criteria[infoIndex] && (
                <div className="info-panel" onClick={e => e.stopPropagation()}>
                    <div className="info-panel-header">
                        <span className="info-panel-title">{criteria[infoIndex].name}</span>
                        <button className="close-btn" onClick={() => setInfoIndex(null)}><FaIcon className="fas fa-times" /></button>
                    </div>
                    <div className="info-panel-body" dangerouslySetInnerHTML={{ __html: parseToHtml(String(criteria[infoIndex].value || 'Описание отсутствует')) }} />
                </div>
            )}
        </div>
    );
};

/* Просмотр переписки уже оценённого чата (из развёрнутой строки журнала).
 * Док справа, а не модалка: критерии оценки остаются слева и прокручиваются,
 * пока читаешь переписку. Страница поджимается классом body.chat-dock-open. */
const ChatViewModal = ({ isOpen, onClose, snapshotId, quotes, userId, title }) => {
    const [snapshot, setSnapshot] = useState(null);
    const [error, setError] = useState('');
    const dockRef = useRef(null);

    useEffect(() => {
        if (!isOpen || !snapshotId) return;
        setSnapshot(null); setError('');
        authFetch(`${API_BASE_URL}/api/c2d_eval/snapshots/${snapshotId}`, { headers: { 'X-User-Id': userId } })
            .then(r => r.json().then(d => ({ ok: r.ok, d })))
            .then(({ ok, d }) => {
                if (ok && d.status === 'success') setSnapshot(d.snapshot);
                else setError(d.error || 'Не удалось загрузить переписку');
            })
            .catch(() => setError('Сетевая ошибка'));
    }, [isOpen, snapshotId, userId]);

    // Клик по цитате — проскроллить чат к сообщению и подсветить его (как в
    // «Ваших оценках» у операторов). Поиск ограничен доком: в документе может
    // быть другой экземпляр ленты с теми же data-mid. behavior='auto' —
    // мгновенный переход при открытии, 'smooth' — по клику (как у операторов).
    const scrollToMessage = (messageId, behavior = 'smooth') => {
        if (messageId == null) return;
        const el = dockRef.current?.querySelector(`[data-mid="${messageId}"]`);
        if (!el) return;
        el.scrollIntoView({ behavior, block: 'center' });
        el.classList.add('chat-msg-flash');
        setTimeout(() => el.classList.remove('chat-msg-flash'), 1400);
    };

    // При открытии переписки сразу показать первую цитату СВ (без анимации).
    useEffect(() => {
        if (!isOpen || !snapshot) return;
        const first = (quotes || [])[0]?.messageId;
        if (first == null) return;
        const t = setTimeout(() => scrollToMessage(first, 'auto'), 150);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen, snapshot]);

    // Док не блокирует страницу (прокрутку журнала не глушим) — только поджимает
    // её вбок; Esc закрывает.
    useEffect(() => {
        if (!isOpen) return;
        document.body.classList.add('chat-dock-open');
        const onKeyDown = (e) => { if (e.key === 'Escape') onClose?.(); };
        window.addEventListener('keydown', onKeyDown);
        return () => {
            document.body.classList.remove('chat-dock-open');
            window.removeEventListener('keydown', onKeyDown);
        };
    }, [isOpen, onClose]);

    if (!isOpen) return null;
    const wazzupUrl = wazzupChatUrlFromSnapshot(snapshot);
    return (
        <aside className="chat-dock" ref={dockRef}>
            <div className="chat-dock-header">
                <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 7 }}>
                        <FaIcon className="fas fa-comments" /> Переписка чата
                    </div>
                    {title ? (
                        <div style={{ fontSize: 11.5, color: 'var(--text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</div>
                    ) : null}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                    {wazzupUrl && (
                        <a className="btn btn-secondary btn-sm"
                           href={wazzupUrl} target="_blank" rel="noopener noreferrer"
                           title="Открыть этот чат в Wazzup (там доступна и история старше 45 дней)">
                            <FaIcon className="fas fa-up-right-from-square" /> В Wazzup
                        </a>
                    )}
                    <button className="close-btn" onClick={onClose} title="Закрыть переписку (Esc)">
                        <FaIcon className="fas fa-times" />
                    </button>
                </div>
            </div>
            <div className="chat-dock-body">
                {error ? (
                    <div style={{ padding: '18px 12px', textAlign: 'center', fontSize: 13, color: 'var(--red)' }}>
                        <FaIcon className="fas fa-circle-exclamation" /> {error}
                    </div>
                ) : !snapshot ? (
                    <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-3)', fontSize: 13 }}>
                        <span className="spinner spinner-dark" /> Загрузка переписки…
                    </div>
                ) : (
                    <>
                        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
                            <ChatThread snapshot={snapshot} quotes={quotes || []} height="100%" />
                        </div>
                        {(quotes || []).length > 0 && (
                            <div style={{ flexShrink: 0, maxHeight: '30vh', overflowY: 'auto', overscrollBehavior: 'contain', display: 'flex', flexDirection: 'column', gap: 6 }}>
                                <span className="label" style={{ marginBottom: 0 }}>Цитаты ({quotes.length}) — клик по тексту прокрутит чат к сообщению</span>
                                {(quotes || []).map((q, i) => (
                                    <button key={i} type="button" className="chat-quote-btn"
                                            onClick={() => scrollToMessage(q.messageId)}
                                            title="Показать это место в переписке">
                                        <div style={{ fontSize: 12.5, fontStyle: 'italic', color: 'var(--text)' }}>«{q.text}»</div>
                                        {q.comment && <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-2)' }}>{q.comment}</div>}
                                    </button>
                                ))}
                            </div>
                        )}
                    </>
                )}
            </div>
        </aside>
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
    const [questionResolved, setQuestionResolved] = useState(false);
    const [resolvedFirstContact, setResolvedFirstContact] = useState(false);
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
    const hasAttachedImportedAudio = !!(
        existingEvaluation?.is_imported
        && !callFile
        && (existingEvaluation?.audio_path || audioUrl)
    );
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
                setAppealDate(`${date.getDate().toString().padStart(2,'0')}-${(date.getMonth()+1).toString().padStart(2,'0')}-${date.getFullYear()} ${date.getHours().toString().padStart(2,'0')}:${date.getMinutes().toString().padStart(2,'0')}:${date.getSeconds().toString().padStart(2,'0')}`);
                setScores(initDir?.criteria?.map(()=>'Correct') || []);
                setComments(initDir?.criteria?.map(()=>'') || []);
                setCommentVisible(initDir?.criteria?.map(()=>false) || []);
                setGeneralComment('');
                setCommentVisibleToOperator(true);
                setQuestionResolved(false);
                setResolvedFirstContact(false);
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
                const nextQuestionResolved = !!(
                    existingEvaluation.questionResolved
                    ?? existingEvaluation._rawEvaluation?.question_resolved
                    ?? false
                );
                setQuestionResolved(nextQuestionResolved);
                setResolvedFirstContact(nextQuestionResolved ? !!(
                    existingEvaluation.resolvedFirstContact
                    ?? existingEvaluation._rawEvaluation?.resolved_first_contact
                    ?? false
                ) : false);
                setPhoneNumber(existingEvaluation.phoneNumber || '');
                setAssignedMonth(existingEvaluation.assignedMonth || selectedMonth);
                setAudioUrl(existingEvaluation.audioUrl || null);
                setActualDuration(null); setDurationMismatch(false);
                if (existingEvaluation.appeal_date) {
                    const date = new Date(existingEvaluation.appeal_date);
                    setAppealDate(`${date.getDate().toString().padStart(2,'0')}-${(date.getMonth()+1).toString().padStart(2,'0')}-${date.getFullYear()} ${date.getHours().toString().padStart(2,'0')}:${date.getMinutes().toString().padStart(2,'0')}:${date.getSeconds().toString().padStart(2,'0')}`);
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
            setQuestionResolved(false);
            setResolvedFirstContact(false);
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

    // Для старых импортов audio_path может быть пустым: сервер сам докачает запись
    // из Binotel/Oktell, сохранит её в GCS и вернёт временную ссылку.
    useEffect(() => {
        if (!existingEvaluation?.is_imported || !existingEvaluation?.id || !userId) return;
        let alive = true;
        setAudioError(null);
        getImportedAudioUrl(existingEvaluation.id, userId).then(url => {
            if (!alive) return;
            if (url) setAudioUrl(url);
            else setAudioError('Не удалось загрузить аудио');
        });
        return () => { alive = false; };
    }, [existingEvaluation, userId]);

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
        let d = input.replace(/\D/g,'').slice(0,14);
        let f = '';
        if (d.length > 0) f += d.slice(0,2);
        if (d.length > 2) f += '-' + d.slice(2,4);
        if (d.length > 4) f += '-' + d.slice(4,8);
        if (d.length > 8) f += ' ' + d.slice(8,10);
        if (d.length > 10) f += ':' + d.slice(10,12);
        if (d.length > 12) f += ':' + d.slice(12,14);
        return f;
    };

    const getAppealDateISO = () => {
        if (!appealDate) return null;
        let d = appealDate.replace(/\D/g,'');
        const isRe = existingEvaluation?.id != null;
        if (isRe) { if (d.length < 12) return null; const s = d.length>=14 ? d.slice(12,14) : '00'; return `${d.slice(4,8)}-${d.slice(2,4)}-${d.slice(0,2)}T${d.slice(8,10)}:${d.slice(10,12)}:${s}`; }
        if (d.length !== 14) return null;
        return `${d.slice(4,8)}-${d.slice(2,4)}-${d.slice(0,2)}T${d.slice(8,10)}:${d.slice(10,12)}:${d.slice(12,14)}`;
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
        (!hasAttachedImportedAudio && durationMismatch);

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
            fd.append('question_resolved', String(!!questionResolved));
            fd.append('resolved_first_contact', String(!!questionResolved && !!resolvedFirstContact));
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
                    questionResolved: !!questionResolved,
                    resolvedFirstContact: questionResolved ? !!resolvedFirstContact : null,
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
                            {!existingEvaluation?.isReevaluation && !hasAttachedImportedAudio && (
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
                            {!hasAttachedImportedAudio && (expectedDuration || actualDuration) && (
                                <div className="duration-info">
                                    <span>Ожидаемая: <strong>{fmtSec(expectedDuration)}</strong></span>
                                    <span>Фактическая: <strong>{fmtSec(actualDuration) || '—'}</strong></span>
                                </div>
                            )}
                            {!hasAttachedImportedAudio && durationMismatch && <div className="duration-error"><FaIcon className="fas fa-exclamation-circle" />{audioError}</div>}
                            {audioError && (hasAttachedImportedAudio || !durationMismatch) && <div className="error-text">{audioError}</div>}
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
                                placeholder="DD-MM-YYYY HH:MM:SS"
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
                            <div className="resolution-flags">
                                <label className={`resolution-flag ${questionResolved ? 'active' : ''}`}>
                                    <input
                                        type="checkbox"
                                        checked={!!questionResolved}
                                        onChange={e => {
                                            const checked = e.target.checked;
                                            setQuestionResolved(checked);
                                            if (!checked) setResolvedFirstContact(false);
                                        }}
                                    />
                                    <span className="resolution-flag-box"><FaIcon className="fas fa-check" /></span>
                                    <span>Вопрос решен</span>
                                </label>
                                {questionResolved && (
                                    <label className={`resolution-flag ${resolvedFirstContact ? 'active' : ''}`}>
                                        <input
                                            type="checkbox"
                                            checked={!!resolvedFirstContact}
                                            onChange={e => setResolvedFirstContact(e.target.checked)}
                                        />
                                        <span className="resolution-flag-box"><FaIcon className="fas fa-check" /></span>
                                        <span>Решено с первого обращения</span>
                                    </label>
                                )}
                            </div>
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
    const isSupervisorRole = canonicalRole === 'sv';
    const headedDepartmentId = user?.headed_department_id ?? user?.headedDepartmentId ?? null;
    const isDepartmentHead = headedDepartmentId !== null && headedDepartmentId !== undefined && String(headedDepartmentId) !== '';
    const isBaseAdminRole = canonicalRole === 'admin' || canonicalRole === 'super_admin';
    const isScopedDepartmentHead = isDepartmentHead && canonicalRole !== 'super_admin';
    const isAdminRole = isBaseAdminRole || isDepartmentHead;
    const isGlobalAdminRole = isBaseAdminRole && !isScopedDepartmentHead;
    const canManageFeedbackReportSetting = isGlobalAdminRole || isDepartmentHead;
    const canUseRequests = isAdminRole || isSupervisorRole || isDepartmentHead;
    const canDecideReevaluationRequests = isAdminRole || isDepartmentHead;
    const canUseCalibration = isGlobalAdminRole || isSupervisorRole;
    const canManageCalibrationRooms = isGlobalAdminRole || isSupervisorRole;
    const canUseAnalytics = isAdminRole || isSupervisorRole;
    const canManageEvaluationNotifications = isAdminRole || isDepartmentHead;
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
    const [batchMode, setBatchMode] = useState(false);
    const [selectedBatchIds, setSelectedBatchIds] = useState(() => new Set());
    const [showBatchFeedbackModal, setShowBatchFeedbackModal] = useState(false);
    const [batchModalCalls, setBatchModalCalls] = useState([]);
    const [evalModalMode, setEvalModalMode] = useState('journal');
    const [showRandomModal, setShowRandomModal] = useState(false);
    // Чаты Chat2Desk (ЧМ СЗоВ): модалка выборки, данные выбранного чата, просмотр переписки
    const [showRandomChatModal, setShowRandomChatModal] = useState(false);
    const [chatEvalData, setChatEvalData] = useState(null);      // {snapshot, request, candidates}
    const [chatViewTarget, setChatViewTarget] = useState(null);  // {snapshotId, quotes, title}
    const [evaluationTarget, setEvaluationTarget] = useState(null);
    const [isLoading, setIsLoading] = useState(false);
    const [loadingCallId, setLoadingCallId] = useState(null);
    const [operatorFromToken, setOperatorFromToken] = useState(null);
    const [fromDate, setFromDate] = useState(null);
    const [toDate, setToDate] = useState(null);
    const [viewMode, setViewMode] = useState('normal');
    const [activeSection, setActiveSection] = useState(canUseAnalytics ? 'analytics' : 'journal');
    const appViewAnalyticsKeyRef = useRef('');
    const [reevaluationRequests, setReevaluationRequests] = useState([]);
    const [reevaluationSearch, setReevaluationSearch] = useState('');
    const [isRequestsLoading, setIsRequestsLoading] = useState(false);
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
    const [showNotificationSettings, setShowNotificationSettings] = useState(false);
    const [feedbackReportPreviewSending, setFeedbackReportPreviewSending] = useState(false);
    const [feedbackReportSetting, setFeedbackReportSetting] = useState({
        loading: false,
        saving: false,
        loaded: false,
        enabled: false,
        telegramConnected: false,
        scope: 'none',
        departmentName: ''
    });
    const [evaluationNotifySetting, setEvaluationNotifySetting] = useState({
        loading: false,
        saving: false,
        loaded: false,
        enabled: false,
        telegramConnected: false,
        scope: 'none',
        departmentId: '',
        departmentName: '',
        departments: []
    });
    const operatorsCacheRef = useRef(new Map());
    const callsCacheRef = useRef(new Map());
    const evaluationTargetCacheRef = useRef(new Map());
    const reevaluationRequestsCacheRef = useRef(new Map());
    const calibrationJoinInFlightRef = useRef(new Map());
    const calibrationDetailInFlightRef = useRef(new Map());
    const calibrationDetailCacheRef = useRef(new Map());
    const DEFAULT_MAX_EVALS = 20;

    const buildCurrentSupervisorOption = useCallback(() => {
        if (!userId) return null;
        return {
            id: Number(userId),
            name: userName || 'Мой профиль',
            role: 'sv',
            status: user?.status || user?.user_status || null
        };
    }, [userId, userName, user]);

    const normalizeSupervisorList = useCallback((rows = []) => {
        const normalized = Array.isArray(rows)
            ? rows.filter(Boolean)
            : [];

        if (!isSupervisorRole || !userId) return normalized;

        const hasCurrentSupervisor = normalized.some((sv) => Number(sv?.id) === Number(userId));
        if (hasCurrentSupervisor) return normalized;

        const currentSupervisor = buildCurrentSupervisorOption();
        return currentSupervisor ? [currentSupervisor, ...normalized] : normalized;
    }, [isSupervisorRole, userId, buildCurrentSupervisorOption]);

    // Analytics section state
    const [analyticsSelectedSvId, setAnalyticsSelectedSvId] = useState('');
    const [analyticsSelectedSvData, setAnalyticsSelectedSvData] = useState(null);
    const [analyticsMonth, setAnalyticsMonth] = useState(new Date().toISOString().slice(0, 7));
    const [analyticsLoading, setAnalyticsLoading] = useState(false);
    const [analyticsActiveOperatorsTab, setAnalyticsActiveOperatorsTab] = useState('active');
    const [analyticsViewSortField, setAnalyticsViewSortField] = useState('name');
    const [analyticsViewSortDir, setAnalyticsViewSortDir] = useState('asc');
    const [analyticsAiModal, setAnalyticsAiModal] = useState({ show: false, loading: false, title: '', result: null, error: '' });
    const [analyticsReportModal, setAnalyticsReportModal] = useState({
        show: false,
        format: 'standard',
        departmentId: '',
        supervisorId: ''
    });
    const [analyticsReportDepartments, setAnalyticsReportDepartments] = useState([]);
    const [analyticsReportDepartmentsLoading, setAnalyticsReportDepartmentsLoading] = useState(false);

    const fmtDate = (ds) => {
        if (!ds) return '—';
        try {
            const d = new Date(String(ds).replace(' ','T'));
            if (isNaN(d)) return ds;
            return new Intl.DateTimeFormat('ru-RU', {day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(d).replace(/\./g,'').replace(',','');
        } catch { return ds; }
    };

    const fmtDateOnly = (ds) => {
        if (!ds) return '—';
        try {
            const raw = String(ds).trim();
            const normalized = raw.includes('T') || raw.includes(' ')
                ? raw.replace(' ', 'T')
                : `${raw}T00:00:00`;
            const d = new Date(normalized);
            if (isNaN(d)) return raw;
            return new Intl.DateTimeFormat('ru-RU', {day:'2-digit',month:'2-digit',year:'numeric'}).format(d);
        } catch {
            return String(ds);
        }
    };

    const getFeedbackSlaStatusMeta = (status) => {
        const normalized = String(status || '').trim().toLowerCase();
        if (normalized === 'on_time') return { label: 'В срок', color: 'var(--green)' };
        if (normalized === 'overdue') return { label: 'Просрочено', color: 'var(--red)' };
        if (normalized === 'pending') return { label: 'Ожидается', color: 'var(--amber)' };
        return { label: '—', color: 'var(--text-2)' };
    };

    const getResolutionStatusMeta = (call) => (
        call?.questionResolved
            ? { label: 'Решен', className: 'badge-green' }
            : { label: 'Не решен', className: 'badge-amber' }
    );

    const getFirstContactStatusMeta = (call) => {
        if (!call?.questionResolved) return { label: 'N/A', className: 'badge-muted' };
        return call?.resolvedFirstContact
            ? { label: '1 обращение', className: 'badge-blue' }
            : { label: 'Повторно', className: 'badge-amber' };
    };

    const renderResolutionBadges = (call) => {
        if (!call || call.is_imported) return <span style={{ color: 'var(--text-3)' }}>—</span>;
        const resolutionMeta = getResolutionStatusMeta(call);
        const firstContactMeta = getFirstContactStatusMeta(call);
        return (
            <div className="resolution-badges">
                <span className={`badge ${resolutionMeta.className}`}>
                    <span className="badge-dot" />
                    {resolutionMeta.label}
                </span>
                <span className={`badge ${firstContactMeta.className}`}>
                    {firstContactMeta.label}
                </span>
            </div>
        );
    };

    const months = Array.from({length:12},(_,i) => {
        const d = new Date(new Date().getFullYear(), new Date().getMonth()-i, 1);
        return { value: `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`, label: d.toLocaleString('ru',{month:'long',year:'numeric'}) };
    });

    const getOperatorsCacheKey = useCallback((scopeId) => `${userRole || 'unknown'}:${scopeId || 'none'}`, [userRole]);
    const getCallsCacheKey = useCallback((operatorId, month) => `${operatorId}:${month}`, []);
    const getReevaluationRequestsCacheKey = useCallback((month, requesterId) => `${requesterId || 'user'}:${month || 'all'}`, []);
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
        audio_path: ev.audio_path || null,
        isDraft: ev.is_draft,
        assignedMonth: ev.month,
        isCorrection: ev.is_correction || false,
        is_imported: ev.is_imported || false,
        sv_request: !!ev.sv_request,
        sv_request_comment: ev.sv_request_comment || null,
        sv_request_by: ev.sv_request_by || null,
        sv_request_by_name: ev.sv_request_by_name || null,
        sv_request_by_role: ev.sv_request_by_role || null,
        sv_request_at: ev.sv_request_at || null,
        sv_request_approved: !!ev.sv_request_approved,
        sv_request_approved_by: ev.sv_request_approved_by || null,
        sv_request_approved_by_name: ev.sv_request_approved_by_name || null,
        sv_request_approved_at: ev.sv_request_approved_at || null,
        sv_request_rejected: !!ev.sv_request_rejected,
        sv_request_rejected_by: ev.sv_request_rejected_by || null,
        sv_request_rejected_by_name: ev.sv_request_rejected_by_name || null,
        sv_request_rejected_at: ev.sv_request_rejected_at || null,
        sv_request_reject_comment: ev.sv_request_reject_comment || null,
        commentVisibleToOperator: ev.comment_visible_to_operator !== false,
        questionResolved: !!ev.question_resolved,
        resolvedFirstContact: ev.question_resolved ? !!ev.resolved_first_contact : null,
        feedback: ev.feedback || null,
        feedbackSla: ev.feedback_sla || ev?.feedback?.sla || null,
        _rawEvaluation: ev
    }), []);

    const loadFeedbackReportSetting = useCallback(async () => {
        if (!canManageFeedbackReportSetting || !userId) return;
        setFeedbackReportSetting(prev => ({ ...prev, loading: true }));
        try {
            const r = await authFetch(`${API_BASE_URL}/api/admin/call_feedback_report_setting`, {
                method: 'GET',
                headers: { 'X-User-Id': userId }
            });
            const d = await r.json();
            if (!r.ok || d?.status !== 'success') {
                throw new Error(d?.error || 'Не удалось получить настройку');
            }
            setFeedbackReportSetting(prev => ({
                ...prev,
                loading: false,
                loaded: true,
                enabled: !!d.enabled,
                telegramConnected: !!d.telegram_connected,
                scope: d.scope || 'none',
                departmentName: d.department_name || ''
            }));
        } catch (e) {
            setFeedbackReportSetting(prev => ({
                ...prev,
                loading: false,
                loaded: true
            }));
            emitCallEvaluationToast(`Ошибка загрузки настройки отчёта: ${e.message}`, 'error');
        }
    }, [canManageFeedbackReportSetting, userId]);

    const toggleFeedbackReportSetting = useCallback(async (nextEnabled) => {
        if (!canManageFeedbackReportSetting || !userId) return;
        const previousEnabled = !!feedbackReportSetting.enabled;
        setFeedbackReportSetting(prev => ({
            ...prev,
            enabled: !!nextEnabled,
            saving: true
        }));
        try {
            const r = await authFetch(`${API_BASE_URL}/api/admin/call_feedback_report_setting`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Id': userId
                },
                body: JSON.stringify({ enabled: !!nextEnabled })
            });
            const d = await r.json();
            if (!r.ok || d?.status !== 'success') {
                throw new Error(d?.error || 'Не удалось сохранить настройку');
            }
            setFeedbackReportSetting(prev => ({
                ...prev,
                saving: false,
                loaded: true,
                enabled: !!d.enabled,
                telegramConnected: !!d.telegram_connected,
                scope: d.scope || prev.scope,
                departmentName: d.department_name || prev.departmentName
            }));
            emitCallEvaluationToast(
                d.enabled
                    ? 'Еженедельные отчёты по ОС включены'
                    : 'Еженедельные отчёты по ОС выключены',
                'success'
            );
        } catch (e) {
            setFeedbackReportSetting(prev => ({
                ...prev,
                saving: false,
                enabled: previousEnabled
            }));
            emitCallEvaluationToast(`Ошибка сохранения настройки: ${e.message}`, 'error');
        }
    }, [canManageFeedbackReportSetting, userId, feedbackReportSetting.enabled]);

    const sendFeedbackReportPreview = useCallback(async () => {
        if (!canManageFeedbackReportSetting || !userId || feedbackReportPreviewSending) return;
        setFeedbackReportPreviewSending(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/admin/call_feedback_report_preview`, {
                method: 'POST',
                headers: { 'X-User-Id': userId }
            });
            const d = await r.json();
            if (!r.ok || d?.status !== 'success') {
                throw new Error(d?.error || 'Не удалось сформировать отчёт');
            }
            emitCallEvaluationToast('Excel-отчёт отправлен в Telegram', 'success');
        } catch (e) {
            emitCallEvaluationToast(`Ошибка отправки отчёта: ${e.message}`, 'error');
        } finally {
            setFeedbackReportPreviewSending(false);
        }
    }, [canManageFeedbackReportSetting, userId, feedbackReportPreviewSending]);

    const loadEvaluationNotifySetting = useCallback(async () => {
        if (!canManageEvaluationNotifications || !userId) return;
        setEvaluationNotifySetting(prev => ({ ...prev, loading: true }));
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation/notification_settings`, {
                method: 'GET',
                headers: { 'X-User-Id': userId }
            });
            const d = await r.json();
            if (!r.ok || d?.status !== 'success') {
                throw new Error(d?.error || 'Не удалось получить настройку уведомлений');
            }
            const departmentId = d.department_id == null ? '' : String(d.department_id);
            setEvaluationNotifySetting(prev => ({
                ...prev,
                loading: false,
                loaded: true,
                enabled: !!d.enabled,
                telegramConnected: !!d.telegram_connected,
                scope: d.scope || 'none',
                departmentId,
                departmentName: d.department_name || '',
                departments: Array.isArray(d.departments) ? d.departments : []
            }));
        } catch (e) {
            setEvaluationNotifySetting(prev => ({
                ...prev,
                loading: false,
                loaded: true
            }));
            emitCallEvaluationToast(`Ошибка загрузки уведомлений: ${e.message}`, 'error');
        }
    }, [canManageEvaluationNotifications, userId]);

    const saveEvaluationNotifySetting = useCallback(async ({ enabled, departmentId }) => {
        if (!canManageEvaluationNotifications || !userId) return;
        const nextDepartmentId = departmentId == null ? evaluationNotifySetting.departmentId : String(departmentId || '');
        if (enabled && !nextDepartmentId) {
            emitCallEvaluationToast('Выберите отдел для уведомлений', 'error');
            return;
        }

        const previousState = evaluationNotifySetting;
        setEvaluationNotifySetting(prev => ({
            ...prev,
            enabled: !!enabled,
            departmentId: nextDepartmentId,
            saving: true
        }));
        try {
            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation/notification_settings`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Id': userId
                },
                body: JSON.stringify({
                    enabled: !!enabled,
                    department_id: nextDepartmentId ? Number(nextDepartmentId) : null
                })
            });
            const d = await r.json();
            if (!r.ok || d?.status !== 'success') {
                throw new Error(d?.error || 'Не удалось сохранить настройку уведомлений');
            }
            const savedDepartmentId = d.department_id == null ? '' : String(d.department_id);
            setEvaluationNotifySetting(prev => ({
                ...prev,
                saving: false,
                loaded: true,
                enabled: !!d.enabled,
                telegramConnected: !!d.telegram_connected,
                scope: d.scope || prev.scope,
                departmentId: savedDepartmentId,
                departmentName: d.department_name || '',
                departments: Array.isArray(d.departments) ? d.departments : prev.departments
            }));
            emitCallEvaluationToast(d.enabled ? 'Уведомления об оценках включены' : 'Уведомления об оценках выключены', 'success');
        } catch (e) {
            setEvaluationNotifySetting({
                ...previousState,
                saving: false
            });
            emitCallEvaluationToast(`Ошибка сохранения уведомлений: ${e.message}`, 'error');
        }
    }, [canManageEvaluationNotifications, userId, evaluationNotifySetting]);

    useEffect(() => {
        const sectionId = normalizeAnalyticsToken(activeSection) || 'journal';
        const roleId = normalizeAnalyticsToken(canonicalRole);
        const analyticsKey = `${sectionId}|${roleId}`;
        if (appViewAnalyticsKeyRef.current === analyticsKey) return;

        appViewAnalyticsKeyRef.current = analyticsKey;
        const wasForwardedToParent = notifyEmbeddedCallEvaluationSectionView({
            section: sectionId,
            role: roleId
        });
        if (!wasForwardedToParent) {
            trackCallEvaluationAppView({
                section: sectionId,
                role: roleId
            });
        }
    }, [activeSection, canonicalRole]);

    // Supervisors
    useEffect(() => {
        if (!(isAdminRole || isSupervisorRole || isDepartmentHead) || !userId) return;
        let isCancelled = false;

        authFetch(`${API_BASE_URL}/api/admin/sv_list`, { headers:{'X-User-Id':userId} })
            .then(async (r) => {
                const d = await readJsonSafe(r);
                if (!r.ok || d?.status !== 'success') {
                    throw new Error(d?.error || `Failed to fetch supervisor list: ${r.status}`);
                }
                return normalizeSupervisorList(d.sv_list || []);
            })
            .then((nextSupervisors) => {
                if (!isCancelled) setSupervisors(nextSupervisors);
            })
            .catch((error) => {
                if (isCancelled) return;
                console.error(error);
                setSupervisors(normalizeSupervisorList([]));
            });

        return () => { isCancelled = true; };
    }, [isAdminRole, isSupervisorRole, isDepartmentHead, userId, normalizeSupervisorList]);

    useEffect(() => {
        if (!initialSelection) return;
        const requestedSection = String(initialSelection.section || '').trim().toLowerCase();
        if (
            requestedSection === 'journal' ||
            (requestedSection === 'requests' && canUseRequests) ||
            (requestedSection === 'calibration' && canUseCalibration) ||
            (requestedSection === 'analytics' && canUseAnalytics)
        ) {
            setActiveSection(requestedSection);
        }
        const id = Number(initialSelection.operatorId);
        if (id) {
            setOperatorFromToken({
                id,
                name: initialSelection.operatorName || ''
            });
        }
        if (initialSelection.month) {
            setSelectedMonth(initialSelection.month);
            setAnalyticsMonth(initialSelection.month);
        }
        if (initialSelection.supervisorId != null) {
            const nextSupervisorId = Number(initialSelection.supervisorId) || null;
            setSelectedSupervisor(nextSupervisorId);
            if (nextSupervisorId) setAnalyticsSelectedSvId(String(nextSupervisorId));
        }
    }, [initialSelection, canUseAnalytics, canUseCalibration, canUseRequests]);

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
        if (activeSection !== 'analytics' || analyticsSelectedSvId) return;
        if (selectedSupervisor) {
            setAnalyticsSelectedSvId(String(selectedSupervisor));
            return;
        }
        if (isSupervisorRole && userId) {
            setAnalyticsSelectedSvId(String(userId));
        }
    }, [activeSection, analyticsSelectedSvId, selectedSupervisor, isSupervisorRole, userId]);

    useEffect(() => {
        if (!userId) return;
        writeEmbedState({
            user: { id: userId, role: userRole, name: userName },
            initialSelection: {
                operatorId: selectedOperator?.id || null,
                operatorName: selectedOperator?.name || '',
                supervisorId: selectedSupervisor || null,
                month: selectedMonth,
                section: activeSection
            }
        });
    }, [userId, userRole, userName, selectedOperator, selectedSupervisor, selectedMonth, activeSection]);

    useEffect(() => {
        if (operatorFromToken && operators.length > 0) {
            const matchedOperator = operators.find(op => Number(op.id) === Number(operatorFromToken.id)) || null;
            if (!matchedOperator) return;
            setSelectedOperator(matchedOperator);
            setOperatorFromToken(null);
        }
    }, [operators, operatorFromToken]);

    useEffect(() => {
        if (!canManageFeedbackReportSetting || !userId) {
            setFeedbackReportSetting({
                loading: false,
                saving: false,
                loaded: false,
                enabled: false,
                telegramConnected: false,
                scope: 'none',
                departmentName: ''
            });
            return;
        }
        if (activeSection !== 'journal') return;
        loadFeedbackReportSetting();
    }, [canManageFeedbackReportSetting, userId, activeSection, loadFeedbackReportSetting]);

    useEffect(() => {
        if (!canManageEvaluationNotifications || !userId) {
            setEvaluationNotifySetting({
                loading: false,
                saving: false,
                loaded: false,
                enabled: false,
                telegramConnected: false,
                scope: 'none',
                departmentId: '',
                departmentName: '',
                departments: []
            });
            return;
        }
        if (activeSection !== 'journal') return;
        loadEvaluationNotifySetting();
    }, [canManageEvaluationNotifications, userId, activeSection, loadEvaluationNotifySetting]);

    useEffect(() => {
        if (!showNotificationSettings) return undefined;
        const handleKeyDown = (event) => {
            if (event.key === 'Escape') setShowNotificationSettings(false);
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [showNotificationSettings]);

    useEffect(() => {
        if (activeSection !== 'journal') setShowNotificationSettings(false);
    }, [activeSection]);

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
        const shouldUseSelectedSupervisor =
            isSupervisorRole ||
            (isAdminRole && (!isScopedDepartmentHead || selectedSupervisor));
        const scopeId = shouldUseSelectedSupervisor ? selectedSupervisor : userId;
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
                    const nextOperators = shouldUseSelectedSupervisor && hasSupervisorMeta ? filteredOperators : rawOperators;
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
    }, [userId, isAdminRole, isSupervisorRole, isScopedDepartmentHead, selectedSupervisor, getOperatorsCacheKey]);

    // Evaluations fetch
    const fetchEvaluations = useCallback(async ({ force = false } = {}) => {
        if (!selectedOperator || !userId) { setCalls([]); setEvaluationTarget(null); return; }
        const isOperatorFromLoadedList = operators.some(op => Number(op.id) === Number(selectedOperator.id));
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

    const fetchReevaluationRequests = useCallback(async ({ force = false } = {}) => {
        if (!userId || !canUseRequests) {
            setReevaluationRequests([]);
            return;
        }

        const cacheKey = getReevaluationRequestsCacheKey(selectedMonth, userId);

        if (!force && reevaluationRequestsCacheRef.current.has(cacheKey)) {
            setReevaluationRequests(reevaluationRequestsCacheRef.current.get(cacheKey) || []);
            return;
        }

        setIsRequestsLoading(true);
        try {
            const params = new URLSearchParams({ month: selectedMonth });

            const r = await authFetch(`${API_BASE_URL}/api/call_evaluation/requests?${params.toString()}`, {
                headers: { 'X-User-Id': userId }
            });
            const d = await r.json();
            if (!r.ok || d?.status !== 'success') {
                throw new Error(d?.error || 'Не удалось загрузить журнал запросов');
            }

            const nextRequests = Array.isArray(d.requests) ? d.requests : [];
            reevaluationRequestsCacheRef.current.set(cacheKey, nextRequests);
            setReevaluationRequests(nextRequests);
        } catch (e) {
            emitCallEvaluationToast('Ошибка загрузки журнала запросов: ' + e.message, 'error');
        } finally {
            setIsRequestsLoading(false);
        }
    }, [
        userId,
        canUseRequests,
        selectedMonth,
        getReevaluationRequestsCacheKey
    ]);

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
        if (activeSection !== 'requests') return;
        fetchReevaluationRequests();
    }, [activeSection, fetchReevaluationRequests]);

    useEffect(() => {
        window.__callEvaluationSetSection = (section) => {
            const normalizedSection = String(section || '').trim().toLowerCase();
            if (
                normalizedSection === 'journal' ||
                (normalizedSection === 'requests' && canUseRequests) ||
                (normalizedSection === 'calibration' && canUseCalibration) ||
                (normalizedSection === 'analytics' && canUseAnalytics)
            ) {
                setActiveSection(normalizedSection);
            }
        };
        window.__callEvaluationFocus = () => {
            callsCacheRef.current.clear();
            evaluationTargetCacheRef.current.clear();
            reevaluationRequestsCacheRef.current.clear();
            fetchEvaluations({ force: true });
            if (activeSection === 'requests') {
                fetchReevaluationRequests({ force: true });
            }
            if (activeSection === 'calibration') {
                calibrationDetailCacheRef.current.clear();
                fetchCalibrationRooms();
            }
        };
        return () => { window.__callEvaluationFocus = null; window.__callEvaluationSetSection = null; };
    }, [fetchEvaluations, fetchReevaluationRequests, fetchCalibrationRooms, activeSection, canUseAnalytics, canUseCalibration, canUseRequests]);

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
                questionResolved: !!data.questionResolved,
                resolvedFirstContact: data.questionResolved ? !!data.resolvedFirstContact : null,
                feedback: null,
                sv_request: false, sv_request_approved: false,
                _rawEvaluation: {
                    comment_visible_to_operator: data.commentVisibleToOperator !== false,
                    question_resolved: !!data.questionResolved,
                    resolved_first_contact: data.questionResolved ? !!data.resolvedFirstContact : null
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

    // После «Случайного звонка»: сбрасываем кэш затронутых месяцев и показываем результат.
    // Если звонок попал в другой месяц (период мог его захватить) — переключаемся на него.
    const handleRandomCallsImported = useCallback((monthsSet, lastMonth) => {
        if (selectedOperator?.id && monthsSet && typeof monthsSet.forEach === 'function') {
            monthsSet.forEach(mo => {
                const key = getCallsCacheKey(selectedOperator.id, mo);
                callsCacheRef.current.delete(key);
                evaluationTargetCacheRef.current.delete(key);
            });
        }
        const canSwitch = lastMonth && lastMonth !== selectedMonth && months.some(m => m.value === lastMonth);
        if (canSwitch) {
            setSelectedMonth(lastMonth); // смена месяца сама перезагрузит журнал (useEffect выше)
        } else {
            fetchEvaluations({ force: true });
        }
    }, [selectedOperator, getCallsCacheKey, months, selectedMonth, fetchEvaluations]);

    const handleFeedbackSaved = useCallback(async () => {
        setShowFeedbackModal(false);
        setFeedbackTargetCall(null);
        await fetchEvaluations({ force: true });
    }, [fetchEvaluations]);

    // Оценка доступна для пакетной ОС: отправленная (не черновик/не импорт) и ещё без ОС.
    const isBatchEligible = useCallback((call) => (
        (isAdminRole || isSupervisorRole) && !!call && !call.is_imported && !call.isDraft && !call.feedback
    ), [isAdminRole, isSupervisorRole]);

    const exitBatchMode = useCallback(() => {
        setBatchMode(false);
        setSelectedBatchIds(new Set());
    }, []);

    const toggleBatchSelection = useCallback((callId) => {
        setSelectedBatchIds(prev => {
            const next = new Set(prev);
            if (next.has(callId)) next.delete(callId); else next.add(callId);
            return next;
        });
    }, []);

    const handleBatchFeedbackSaved = useCallback(async () => {
        setShowBatchFeedbackModal(false);
        setBatchMode(false);
        setSelectedBatchIds(new Set());
        await fetchEvaluations({ force: true });
    }, [fetchEvaluations]);

    // Сбрасываем пакетный выбор при смене контекста журнала, чтобы не остались
    // выбранными оценки другого оператора/месяца/раздела.
    useEffect(() => {
        setBatchMode(false);
        setSelectedBatchIds(new Set());
    }, [selectedOperator, selectedMonth, activeSection]);

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

    // Пакетная ОС: какие из показанных оценок можно отметить и что сейчас выбрано.
    const batchEligibleCalls = displayedCalls.filter(isBatchEligible);
    const allBatchSelected = batchEligibleCalls.length > 0 && batchEligibleCalls.every(c => selectedBatchIds.has(c.id));
    const selectedBatchCalls = displayedCalls.filter(c => selectedBatchIds.has(c.id) && isBatchEligible(c));
    const toggleSelectAllBatch = () => {
        setSelectedBatchIds(prev => {
            const next = new Set(prev);
            if (batchEligibleCalls.every(c => next.has(c.id))) {
                batchEligibleCalls.forEach(c => next.delete(c.id));
            } else {
                batchEligibleCalls.forEach(c => next.add(c.id));
            }
            return next;
        });
    };

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
    const analyticsSelectedSupervisorObj = analyticsSelectedSvId ? supervisors.find(sv => Number(sv.id) === Number(analyticsSelectedSvId)) : null;
    const analyticsSelectedSupervisorIsFired = isFiredStatus(analyticsSelectedSupervisorObj?.status);
    const selectedOperatorIsFired = isFiredStatus(selectedOperator?.status);
    // «Случайный звонок»: СЗоВ (Oktell) и TEZ (Binotel) — признак считает бэкенд
    // (/api/admin/directions -> random_call_eligible + random_call_source). Прочие
    // отделы/направления кнопку не видят. source управляет модалкой (у TEZ — свои min/max).
    const selectedOperatorDirectionMeta = (directions || []).find(d => Number(d.id) === Number(selectedOperator?.direction_id)) || null;
    const isOperatorModelDirection = !!selectedOperatorDirectionMeta?.random_call_eligible;
    // «Случайный чат»: ЧМ СЗоВ (Chat2Desk) и Верификаторы ОП (Wazzup) — признак
    // и источник (random_chat_source) считает бэкенд.
    const isRandomChatDirection = !!selectedOperatorDirectionMeta?.random_chat_eligible;
    const randomChatSource = selectedOperatorDirectionMeta?.random_chat_source || 'chat2desk';
    const sectionTitle = activeSection === 'requests'
        ? 'Журнал запросов'
        : activeSection === 'calibration'
            ? 'Калибровка звонков'
            : 'Журнал оценок';
    const reevaluationSearchNormalized = String(reevaluationSearch || '').trim().toLowerCase();
    const filteredReevaluationRequests = reevaluationRequests.filter((item) => {
        if (!reevaluationSearchNormalized) return true;
        const searchableParts = [
            item?.id,
            item?.operator_name,
            item?.supervisor_name,
            item?.sv_request_by_name,
            item?.phone_number,
            item?.direction?.name,
            item?.sv_request_comment,
            item?.sv_request_reject_comment,
            item?.correction_call_id,
            item?.correction_evaluator_name
        ];
        return searchableParts.some((value) => String(value || '').toLowerCase().includes(reevaluationSearchNormalized));
    });
    const requestStats = filteredReevaluationRequests.reduce((acc, item) => {
        acc.total += 1;
        if (item?.correction_call_id) acc.completed += 1;
        else if (item?.sv_request_approved) acc.approved += 1;
        else if (item?.sv_request_rejected) acc.rejected += 1;
        else acc.pending += 1;
        return acc;
    }, { total: 0, pending: 0, approved: 0, completed: 0, rejected: 0 });
    const requestFooterInfo = [
        reevaluationSearchNormalized
            ? `${filteredReevaluationRequests.length} из ${reevaluationRequests.length} записей`
            : `${requestStats.total} записей`,
        months.find(m => m.value === selectedMonth)?.label || selectedMonth
    ].filter(Boolean).join(' · ');
    const handleRequestsUpdated = useCallback(async () => {
        reevaluationRequestsCacheRef.current.clear();
        await fetchReevaluationRequests({ force: true });
        if (selectedOperator?.id) {
            callsCacheRef.current.delete(getCallsCacheKey(selectedOperator.id, selectedMonth));
            await fetchEvaluations({ force: true });
        }
    }, [fetchReevaluationRequests, selectedOperator, selectedMonth, getCallsCacheKey, fetchEvaluations]);
    const openRequestInJournal = useCallback((requestItem) => {
        if (!requestItem?.operator_id) return;
        if (requestItem.supervisor_id) {
            setSelectedSupervisor(Number(requestItem.supervisor_id) || null);
        }
        setOperatorFromToken({
            id: Number(requestItem.operator_id),
            name: requestItem.operator_name || ''
        });
        setExpandedId(requestItem.id);
        setActiveSection('journal');
    }, []);

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

    // ─── Analytics helpers ───────────────────────────────────────────────────
    const getAnalyticsMonthOptions = () => {
        const opts = [];
        const today = new Date();
        for (let i = 0; i < 12; i++) {
            const d = new Date(today.getFullYear(), today.getMonth() - i, 1);
            const val = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
            opts.push(<option key={val} value={val}>{d.toLocaleString('ru-RU',{month:'long',year:'numeric'})}</option>);
        }
        return opts;
    };

    const getAnalyticsCurrentWeek = () => {
        const today = new Date();
        const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
        return Math.ceil((today.getDate() + firstDay.getDay() - 1) / 7);
    };

    const getAnalyticsExpectedCalls = (week) => week * 5;

    const getAnalyticsEvaluationPlanMeta = (op) => {
        const target = op?.evaluation_target;
        if (!target || typeof target !== 'object') return null;
        const requiredCalls = Number(target?.required_calls);
        if (!Number.isFinite(requiredCalls) || requiredCalls < 0) return null;
        const workedHoursUsed = Number(target?.worked_hours_used ?? target?.accounted_hours ?? 0);
        const normHours = Number(target?.full_rate_norm_hours ?? target?.norm_hours ?? 0);
        const baseCallTarget = Number(target?.base_call_target ?? 0);
        const requiredCallsRaw = Number(target?.required_calls_raw ?? requiredCalls);
        return {
            requiredCalls,
            workedHoursUsed: Number.isFinite(workedHoursUsed) ? workedHoursUsed : 0,
            normHours: Number.isFinite(normHours) ? normHours : 0,
            baseCallTarget: Number.isFinite(baseCallTarget) ? baseCallTarget : 0,
            requiredCallsRaw: Number.isFinite(requiredCallsRaw) ? requiredCallsRaw : requiredCalls,
        };
    };

    const getAnalyticsNormTone = (pct) => {
        const value = Number(pct);
        if (!Number.isFinite(value)) {
            return {
                background: 'var(--surface-2)',
                color: 'var(--text-2)',
                borderColor: 'var(--border)'
            };
        }
        if (value >= 95) {
            return {
                background: 'var(--green-light)',
                color: 'var(--green)',
                borderColor: '#bbf7d0'
            };
        }
        if (value >= 50) {
            return {
                background: 'var(--amber-light)',
                color: 'var(--amber)',
                borderColor: '#fde68a'
            };
        }
        return {
            background: 'var(--red-light)',
            color: 'var(--red)',
            borderColor: '#fecaca'
        };
    };

    const renderAnalyticsPlanContent = (op, callCount) => {
        const meta = getAnalyticsEvaluationPlanMeta(op);
        if (!meta) return <span className="analytics-plan-chip">{callCount}</span>;
        const pct = meta.requiredCalls > 0 ? (Number(callCount || 0) / meta.requiredCalls) * 100 : 100;
        const tone = getAnalyticsNormTone(pct);
        const formula = `Расчет: (${meta.workedHoursUsed.toFixed(2)} ч / ${meta.normHours.toFixed(2)} ч полной ставки) × ${meta.baseCallTarget} = ${meta.requiredCallsRaw.toFixed(2)}, итог ${meta.requiredCalls}`;
        return (
            <div className="analytics-plan">
                <div className="analytics-plan-chip" style={tone}>{callCount} / {meta.requiredCalls}</div>
                <div className="analytics-plan-tooltip">{formula}</div>
            </div>
        );
    };

    const handleAnalyticsSort = (field) => {
        if (analyticsViewSortField === field) setAnalyticsViewSortDir(prev => prev === 'asc' ? 'desc' : 'asc');
        else { setAnalyticsViewSortField(field); setAnalyticsViewSortDir('asc'); }
    };

    const getAnalyticsSortIcon = (field) => {
        if (analyticsViewSortField !== field) return <span className="ml-1 text-gray-400 text-xs">⇅</span>;
        return analyticsViewSortDir === 'asc' ? <span className="ml-1 text-xs">▲</span> : <span className="ml-1 text-xs">▼</span>;
    };

    const compareAnalyticsByField = (a, b) => {
        const dir = analyticsViewSortDir === 'asc' ? 1 : -1;
        const nameA = (a?.name || '').toString();
        const nameB = (b?.name || '').toString();
        const nameCmp = nameA.localeCompare(nameB, 'ru', {sensitivity:'base'});
        switch (analyticsViewSortField) {
            case 'listened': { const ca = parseInt(a.call_count)||0, cb = parseInt(b.call_count)||0; return (ca-cb)*dir || nameCmp; }
            case 'avg_score': { const sa = a.avg_score==null?-1:Number(a.avg_score), sb = b.avg_score==null?-1:Number(b.avg_score); return (sa-sb)*dir || nameCmp; }
            case 'feedback': { const fa = Number(a.feedback_count)||0, fb = Number(b.feedback_count)||0; return (fa-fb)*dir || nameCmp; }
            case 'percent': {
                const getPct = (op) => { const meta = getAnalyticsEvaluationPlanMeta(op); const t = meta?.requiredCalls ?? getAnalyticsExpectedCalls(getAnalyticsCurrentWeek()); return t > 0 ? ((parseInt(op.call_count)||0)/t)*100 : 0; };
                return (getPct(a)-getPct(b))*dir || nameCmp;
            }
            default: return nameCmp * dir;
        }
    };

    // ─── Analytics API ───────────────────────────────────────────────────────
    const fetchAnalyticsSvData = useCallback(async (svId, month) => {
        if (!svId) { setAnalyticsSelectedSvData(null); return; }
        setAnalyticsLoading(true);
        try {
            const url = `${API_BASE_URL}/api/sv/data?id=${encodeURIComponent(svId)}${month ? `&month=${encodeURIComponent(month)}` : ''}`;
            const r = await authFetch(url, { headers: { 'X-User-Id': userId } });
            const d = await readJsonSafe(r);
            if (d?.status === 'success') setAnalyticsSelectedSvData(d);
            else emitCallEvaluationToast(d?.error || 'Ошибка загрузки данных', 'error');
        } catch { emitCallEvaluationToast('Ошибка загрузки данных', 'error'); }
        finally { setAnalyticsLoading(false); }
    }, [userId]);

    const loadAnalyticsReportDepartments = useCallback(async () => {
        if (!isGlobalAdminRole || !userId) {
            setAnalyticsReportDepartments([]);
            return;
        }
        if (analyticsReportDepartments.length > 0 || analyticsReportDepartmentsLoading) return;
        setAnalyticsReportDepartmentsLoading(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/admin/departments`, {
                headers: { 'X-User-Id': userId }
            });
            const d = await readJsonSafe(r);
            if (!r.ok || d?.status !== 'success') {
                throw new Error(d?.error || 'Не удалось загрузить отделы');
            }
            const nextDepartments = Array.isArray(d.departments)
                ? d.departments.filter((dept) => dept && dept.is_active !== false)
                : [];
            setAnalyticsReportDepartments(nextDepartments);
        } catch (e) {
            emitCallEvaluationToast(`Ошибка загрузки отделов: ${e.message}`, 'error');
        } finally {
            setAnalyticsReportDepartmentsLoading(false);
        }
    }, [isGlobalAdminRole, userId, analyticsReportDepartments.length, analyticsReportDepartmentsLoading]);

    const openAnalyticsReportModal = useCallback(() => {
        setAnalyticsReportModal(prev => ({
            show: true,
            format: prev.format || 'standard',
            departmentId: prev.departmentId || '',
            supervisorId: prev.supervisorId || analyticsSelectedSvId || ''
        }));
        if (isGlobalAdminRole) {
            loadAnalyticsReportDepartments();
        }
    }, [isGlobalAdminRole, loadAnalyticsReportDepartments, analyticsSelectedSvId]);

    const closeAnalyticsReportModal = useCallback(() => {
        if (analyticsLoading) return;
        setAnalyticsReportModal(prev => ({ ...prev, show: false }));
    }, [analyticsLoading]);

    const analyticsGenerateReport = useCallback(async (options = {}) => {
        if (options && typeof options.preventDefault === 'function') {
            options.preventDefault();
        }
        if (!options || options.confirmed !== true) {
            openAnalyticsReportModal();
            return;
        }

        const { format = 'standard', departmentId = '', supervisorId = '' } = options;
        const normalizedFormat = format === 'dates'
            ? 'dates'
            : format === 'group'
                ? 'group'
                : 'standard';
        if (normalizedFormat === 'group' && !supervisorId) {
            emitCallEvaluationToast('Выберите супервайзера для выгрузки по группе', 'error');
            return;
        }
        setAnalyticsLoading(true);
        try {
            const query = new URLSearchParams({
                month: analyticsMonth,
                format: normalizedFormat
            });
            if (isGlobalAdminRole && departmentId) {
                query.set('department_id', departmentId);
            }
            if (normalizedFormat === 'group') {
                query.set('supervisor_id', String(supervisorId));
            }
            const r = await authFetch(`${API_BASE_URL}/api/admin/monthly_report?${query.toString()}`, { headers: { 'X-User-Id': userId } });
            if (!r.ok) {
                const d = await readJsonSafe(r);
                throw new Error(d?.error || 'Ошибка генерации отчёта');
            }

            const blob = await r.blob();
            const contentDisposition = r.headers.get('content-disposition') || '';
            let filename = normalizedFormat === 'dates'
                ? `monthly_report_dates_${analyticsMonth}.xlsx`
                : normalizedFormat === 'group'
                    ? `journal_by_group_${analyticsMonth}.xlsx`
                    : `monthly_report_${analyticsMonth}.xlsx`;
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

            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            setAnalyticsReportModal(prev => ({ ...prev, show: false }));
            emitCallEvaluationToast('Отчёт скачан', 'success');
        } catch (e) {
            emitCallEvaluationToast(e.message || 'Ошибка генерации отчёта', 'error');
        }
        finally { setAnalyticsLoading(false); }
    }, [analyticsMonth, userId, isGlobalAdminRole, openAnalyticsReportModal]);

    const analyticsNotifySv = useCallback(async (svId, operatorName, callCount, targetCalls) => {
        setAnalyticsLoading(true);
        try {
            const r = await authFetch(`${API_BASE_URL}/api/admin/notify_sv`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-User-Id': userId },
                body: JSON.stringify({ sv_id: svId, operator_name: operatorName, call_count: callCount, target_calls: targetCalls })
            });
            const d = await readJsonSafe(r);
            if (d?.status === 'success') emitCallEvaluationToast('Уведомление отправлено', 'success');
            else emitCallEvaluationToast(d?.error || 'Ошибка отправки', 'error');
        } catch { emitCallEvaluationToast('Ошибка отправки уведомления', 'error'); }
        finally { setAnalyticsLoading(false); }
    }, [userId]);

    const analyticsOpenAiFeedback = useCallback(async (operatorId, operatorName, month) => {
        if (!operatorId || !month) return;
        setAnalyticsAiModal({ show: true, loading: true, title: `${operatorName || 'Оператор'} · ${month}`, result: null, error: '' });
        try {
            const r = await authFetch(`${API_BASE_URL}/api/ai/monthly_feedback`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-User-Id': userId },
                body: JSON.stringify({ operator_id: operatorId, month })
            });
            const d = await readJsonSafe(r);
            if (d?.status === 'success') setAnalyticsAiModal(prev => ({ ...prev, loading: false, result: d.result || null }));
            else setAnalyticsAiModal(prev => ({ ...prev, loading: false, error: d?.error || 'Ошибка запроса' }));
        } catch (e) { setAnalyticsAiModal(prev => ({ ...prev, loading: false, error: e.message || 'Ошибка запроса' })); }
    }, [userId]);

    const analyticsEffectiveSvId = analyticsSelectedSvId;

    const getAnalyticsOperatorSupervisorId = (op) => {
        const candidates = [
            op?.supervisor_id,
            op?.sv_id,
            op?.supervisorId,
            op?.supervisor?.id,
            op?.evaluation_target?.supervisor_id,
            op?.evaluation_target?.sv_id
        ];
        for (const candidate of candidates) {
            if (candidate == null || candidate === '') continue;
            const numeric = Number(candidate);
            if (Number.isFinite(numeric) && numeric > 0) return String(numeric);
        }
        return '';
    };

    const getAnalyticsScopedOperators = (rows = []) => {
        const list = Array.isArray(rows) ? rows : [];
        const targetSvId = String(analyticsEffectiveSvId || '').trim();
        if (!targetSvId) return list;
        const hasSupervisorMeta = list.some((op) => !!getAnalyticsOperatorSupervisorId(op));
        if (!hasSupervisorMeta) return list;
        return list.filter((op) => getAnalyticsOperatorSupervisorId(op) === targetSvId);
    };

    useEffect(() => {
        if (activeSection === 'analytics' && analyticsEffectiveSvId) {
            fetchAnalyticsSvData(analyticsEffectiveSvId, analyticsMonth);
        } else if (activeSection === 'analytics' && !analyticsEffectiveSvId) {
            setAnalyticsSelectedSvData(null);
        }
    }, [analyticsEffectiveSvId, analyticsMonth, activeSection, fetchAnalyticsSvData]);

    const analyticsScopedOperators = getAnalyticsScopedOperators(analyticsSelectedSvData?.operators ?? []);
    const evaluationNotifyDepartments = Array.isArray(evaluationNotifySetting.departments)
        ? evaluationNotifySetting.departments.filter(Boolean)
        : [];
    const selectedEvaluationNotifyDepartment = evaluationNotifyDepartments.find(
        (dept) => String(dept?.id) === String(evaluationNotifySetting.departmentId)
    );
    const evaluationNotifyDepartmentName = evaluationNotifySetting.departmentName || selectedEvaluationNotifyDepartment?.name || '';
    const evaluationNotifyDisabled = (
        !!evaluationNotifySetting.loading ||
        !!evaluationNotifySetting.saving ||
        !evaluationNotifySetting.telegramConnected ||
        !evaluationNotifySetting.departmentId
    );
    const hasEnabledJournalNotifications = !!evaluationNotifySetting.enabled || (
        canManageFeedbackReportSetting && !!feedbackReportSetting.enabled
    );

    return (
        <div className="app">
            {/* Header */}
            <header className="header">
                <div className="header-logo">
                    <div className="header-logo-dot" />
                    <h1>Журнал Оценок</h1>
                </div>
                <div className="header-right">
                    {(canUseAnalytics || canUseRequests || canUseCalibration) && (
                        <div className="section-switch">
                            {canUseAnalytics && (
                            <button
                                className={`btn btn-sm ${activeSection === 'analytics' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setActiveSection('analytics')}
                            >
                                Аналитика
                            </button>
                            )}
                            <button
                                className={`btn btn-sm ${activeSection === 'journal' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setActiveSection('journal')}
                            >
                                Журнал
                            </button>
                            <button
                                className={`btn btn-sm ${activeSection === 'requests' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setActiveSection('requests')}
                            >
                                Журнал запросов
                            </button>
                        </div>
                    )}
                    {canUseCalibration && (
                        <button
                            className={`btn btn-sm ${activeSection === 'calibration' ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setActiveSection('calibration')}
                        >
                            Калибровка
                        </button>
                    )}
                    {userName && <span className="header-user">{userName}</span>}
                </div>
            </header>

            {/* Main panel */}
            {activeSection !== 'analytics' && (
            <div className="main-panel">
                {/* Panel header with filters */}
                <div className="panel-header">
                    <div className="panel-title-wrap">
                        <span className="panel-title">{sectionTitle}</span>
                    </div>
                    <div className="filters">
                        {(isAdminRole || isSupervisorRole) && activeSection !== 'requests' && (
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
                        {activeSection === 'requests' && (
                            <div className="filter-group" style={{ minWidth: 280 }}>
                                <label className="label">Поиск</label>
                                <input
                                    className="input"
                                    type="text"
                                    value={reevaluationSearch}
                                    onChange={(e) => setReevaluationSearch(e.target.value)}
                                    placeholder="Call ID, оператор, супервайзер, телефон..."
                                />
                            </div>
                        )}
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
                            <thead><tr><th>#</th><th>Статус</th><th>Направление</th><th>Телефон</th><th>Балл</th><th>Решение</th><th>Дата обращения</th><th>Дата оценки</th></tr></thead>
                            <tbody>
                                {[...Array(5)].map((_,i) => (
                                    <tr key={i}><td colSpan={8}><div style={{display:'grid',gridTemplateColumns:'40px 80px 1fr 120px 60px 150px 140px 1fr',gap:8,padding:'12px 16px 12px 20px'}}>
                                        {[40,80,'1fr',120,60,150,140,'1fr'].map((w,j)=><div key={j} className="skeleton" style={{height:16,width:typeof w==='number'?w:'100%'}} />)}
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
                                    {batchMode && (
                                        <th style={{width:36}}>
                                            <input
                                                type="checkbox"
                                                checked={allBatchSelected}
                                                disabled={batchEligibleCalls.length === 0}
                                                onChange={toggleSelectAllBatch}
                                                title="Выбрать все доступные"
                                            />
                                        </th>
                                    )}
                                    <th>#</th>
                                    <th>Статус</th>
                                    <th>Направление</th>
                                    <th>Телефон</th>
                                    <th>Балл</th>
                                    <th>Решение</th>
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
                                            {batchMode && (
                                                <td onClick={e => e.stopPropagation()} style={{textAlign:'center'}}>
                                                    {isBatchEligible(call) ? (
                                                        <input
                                                            type="checkbox"
                                                            checked={selectedBatchIds.has(call.id)}
                                                            onChange={() => toggleBatchSelection(call.id)}
                                                        />
                                                    ) : null}
                                                </td>
                                            )}
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
                                            <td>{renderResolutionBadges(call)}</td>
                                            <td style={{fontSize:12,color:'var(--text-2)'}}>{fmtDate(call.appeal_date)}</td>
                                            <td>
                                                <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:8}}>
                                                    <span style={{fontSize:12,color:'var(--text-2)'}}>{fmtDate(call._rawEvaluation?.evaluation_date||call.date)}</span>
                                                    <div style={{display:'flex',alignItems:'center',gap:6,flexShrink:0,flexWrap:'wrap',justifyContent:'flex-end'}} onClick={e=>e.stopPropagation()}>
                                                        {call.is_imported ? (
                                                            <>
                                                                <button className="btn btn-green btn-sm" onClick={() => { setEvalModalMode('journal'); setEditingEval(call); setShowEvalModal(true); }}><FaIcon className="fas fa-star" /> Оценить</button>
                                                                {isAdminRole && <button className="btn btn-danger btn-sm" onClick={() => deleteImportedCall(call.id)}><FaIcon className="fas fa-trash" /></button>}
                                                            </>
                                                        ) : (
                                                            <>
                                                                {isAdminRole && !call.isDraft && getReevaluationRequestStatus(call) === 'pending' && (
                                                                    <SvRequestButton
                                                                        call={call}
                                                                        userId={userId}
                                                                        userRole={isSupervisorRole ? 'sv' : userRole}
                                                                        isAdminRole={isAdminRole}
                                                                        fetchEvaluations={fetchEvaluations}
                                                                        onUpdated={handleRequestsUpdated}
                                                                        pendingAdminMode="buttons_only"
                                                                        onReevaluate={() => { setEvalModalMode('journal'); setEditingEval({...call,isReevaluation:true}); setShowEvalModal(true); }}
                                                                    />
                                                                )}
                                                                {(isAdminRole || isSupervisorRole) && !call.isDraft && (
                                                                    <button
                                                                        className={`btn btn-sm ${call.feedback ? 'btn-secondary' : 'btn-primary'}`}
                                                                        onClick={() => {
                                                                            if (call.feedback) {
                                                                                setFeedbackTargetCall(call);
                                                                                setShowFeedbackModal(true);
                                                                            } else {
                                                                                // Клик по «ОС» включает режим выбора нескольких оценок
                                                                                // и сразу отмечает текущую.
                                                                                setBatchMode(true);
                                                                                setSelectedBatchIds(prev => {
                                                                                    const next = new Set(prev);
                                                                                    next.add(call.id);
                                                                                    return next;
                                                                                });
                                                                            }
                                                                        }}
                                                                    >
                                                                        <FaIcon className={`fas fa-${call.feedback ? 'pen' : 'comments'}`} />
                                                                        {call.feedback ? 'Ред. ОС' : 'ОС'}
                                                                    </button>
                                                                )}
                                                                {!(isAdminRole && !call.isDraft && getReevaluationRequestStatus(call) === 'pending') && (
                                                                    <SvRequestButton call={call} userId={userId} userRole={isSupervisorRole ? 'sv' : userRole} isAdminRole={isAdminRole} fetchEvaluations={fetchEvaluations} onUpdated={handleRequestsUpdated} onReevaluate={() => { setEvalModalMode('journal'); setEditingEval({...call,isReevaluation:true}); setShowEvalModal(true); }} />
                                                                )}
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
                                                <td colSpan={batchMode ? 9 : 8}>
                                                    <div className="expanded-content">
                                                        <h4>Детали оценки</h4>
                                                        <div className="expanded-meta">
                                                            <div className="expanded-meta-item"><strong>Оценщик:</strong> {call._rawEvaluation?.evaluator || '—'}</div>
                                                            <div className="expanded-meta-item"><strong>Дата оценки:</strong> {fmtDate(call._rawEvaluation?.evaluation_date||call.date)}</div>
                                                            <div className="expanded-meta-item"><strong>Дата обращения:</strong> {fmtDate(call._rawEvaluation?.appeal_date||call.appeal_date)}</div>
                                                            <div className="expanded-meta-item"><strong>Показ оператору:</strong> {call.commentVisibleToOperator !== false ? 'Да' : 'Нет'}</div>
                                                            <div className="expanded-meta-item"><strong>Вопрос решен:</strong> {call.questionResolved ? 'Да' : 'Нет'}</div>
                                                            <div className="expanded-meta-item"><strong>С первого обращения:</strong> {call.questionResolved ? (call.resolvedFirstContact ? 'Да' : 'Нет') : 'N/A'}</div>
                                                        </div>
                                                        <div style={{marginBottom:12, fontSize:13, color:'var(--text-2)'}}>
                                                            <strong style={{color:'var(--text)'}}>Общий комментарий:</strong> {call.combinedComment?.trim() || '—'}
                                                        </div>
                                                        {call.sv_request && (() => {
                                                            const requestStatus = getReevaluationRequestStatus(call);
                                                            const statusLabel = requestStatus === 'approved'
                                                                ? 'Одобрено'
                                                                : requestStatus === 'rejected'
                                                                    ? 'Отклонено'
                                                                    : 'На рассмотрении';
                                                            const statusColor = requestStatus === 'approved'
                                                                ? 'var(--green)'
                                                                : requestStatus === 'rejected'
                                                                    ? 'var(--red)'
                                                                    : 'var(--amber)';
                                                            return (
                                                                <div style={{marginBottom:12, fontSize:13, color:'var(--text-2)', padding:'10px 12px', border:`1px solid ${statusColor}`, borderRadius:'var(--radius)', background:'var(--surface)'}}>
                                                                    <div style={{marginBottom:6}}>
                                                                        <strong style={{color:'var(--text)'}}>Запрос на переоценку:</strong>{' '}
                                                                        <span style={{color: statusColor, fontWeight: 600}}>{statusLabel}</span>
                                                                    </div>
                                                                    <div style={{display:'flex', flexWrap:'wrap', gap:'6px 14px', marginBottom:(call.sv_request_comment || call.sv_request_reject_comment) ? 6 : 0}}>
                                                                        <span><strong style={{color:'var(--text)'}}>Инициатор:</strong> {call.sv_request_by_name || '—'}{call.sv_request_by_role ? ` (${getReevaluationRequestRoleLabel(call.sv_request_by_role)})` : ''}</span>
                                                                        <span><strong style={{color:'var(--text)'}}>Создан:</strong> {call.sv_request_at || '—'}</span>
                                                                        {call.sv_request_approved_by_name ? <span><strong style={{color:'var(--text)'}}>Одобрил:</strong> {call.sv_request_approved_by_name}</span> : null}
                                                                        {call.sv_request_rejected_by_name ? <span><strong style={{color:'var(--text)'}}>Отклонил:</strong> {call.sv_request_rejected_by_name}</span> : null}
                                                                    </div>
                                                                    {call.sv_request_comment ? (
                                                                        <div style={{marginBottom: call.sv_request_reject_comment ? 4 : 0}}>
                                                                            <strong style={{color:'var(--text)'}}>Комментарий:</strong> {call.sv_request_comment}
                                                                        </div>
                                                                    ) : null}
                                                                    {call.sv_request_reject_comment ? (
                                                                        <div>
                                                                            <strong style={{color:'var(--text)'}}>Причина отклонения:</strong> {call.sv_request_reject_comment}
                                                                        </div>
                                                                    ) : null}
                                                                </div>
                                                            );
                                                        })()}
                                                        {(() => {
                                                            const sla = call.feedbackSla || call.feedback?.sla || null;
                                                            if (!sla) return null;
                                                            const statusMeta = getFeedbackSlaStatusMeta(sla.status);
                                                            return (
                                                                <div style={{marginBottom:12, fontSize:13, color:'var(--text-2)', padding:'10px 12px', border:'1px solid var(--border)', borderRadius:'var(--radius)', background:'var(--surface)'}}>
                                                                    <div style={{marginBottom:4}}>
                                                                        <strong style={{color:'var(--text)'}}>SLA по ОС:</strong>{' '}
                                                                        <span style={{color: statusMeta.color, fontWeight: 600}}>{statusMeta.label}</span>
                                                                    </div>
                                                                    <div style={{display:'flex', flexWrap:'wrap', gap:'6px 14px'}}>
                                                                        <span><strong style={{color:'var(--text)'}}>Дедлайн:</strong> {fmtDateOnly(sla.due_date)}</span>
                                                                        <span><strong style={{color:'var(--text)'}}>Срок:</strong> {Number(sla.deadline_days) || 0} дн.</span>
                                                                        <span><strong style={{color:'var(--text)'}}>ОС:</strong> {fmtDateOnly(sla.feedback_date)}</span>
                                                                        <span><strong style={{color:'var(--text)'}}>Просрочка:</strong> {Number(sla.overdue_days) > 0 ? `+${Number(sla.overdue_days)} дн.` : '—'}</span>
                                                                        <span><strong style={{color:'var(--text)'}}>Крит. ошибка:</strong> {sla.has_critical_error ? 'Да' : 'Нет'}</span>
                                                                    </div>
                                                                </div>
                                                            );
                                                        })()}
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
                                                        {call._rawEvaluation?.c2d_snapshot_id && (
                                                            <div style={{marginBottom:14}}>
                                                                <button className="btn btn-secondary btn-sm" onClick={() => setChatViewTarget({
                                                                    snapshotId: call._rawEvaluation.c2d_snapshot_id,
                                                                    quotes: call._rawEvaluation.chat_quotes || [],
                                                                    title: `${selectedOperator?.name || ''} · ${call.phoneNumber || ''}`
                                                                })}>
                                                                    <FaIcon className="fas fa-comments" /> Открыть переписку чата
                                                                </button>
                                                                {(call._rawEvaluation.chat_quotes || []).length > 0 && (
                                                                    <div style={{display:'flex',flexDirection:'column',gap:6,marginTop:10,maxWidth:640}}>
                                                                        {(call._rawEvaluation.chat_quotes || []).map((q, qi) => (
                                                                            <div key={qi} style={{borderLeft:'3px solid #f59e0b',background:'var(--surface-2)',borderRadius:'var(--radius)',padding:'8px 10px'}}>
                                                                                <div style={{fontSize:12.5,fontStyle:'italic',color:'var(--text)'}}>«{q.text}»</div>
                                                                                {q.comment && <div style={{marginTop:4,fontSize:12,color:'var(--text-2)'}}>{q.comment}</div>}
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                )}
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
                        {viewMode === 'normal' && isOperatorModelDirection && (
                            <button
                                className={`btn btn-secondary btn-sm ${!selectedOperator ? 'disabled' : ''}`}
                                style={{opacity:!selectedOperator?0.4:1,cursor:!selectedOperator?'not-allowed':'pointer'}}
                                onClick={() => { if (!selectedOperator) return; setShowRandomModal(true); }}
                                disabled={!selectedOperator}
                                title="Взять случайный звонок из Oktell за период (исход/вход), которого ещё не было в оценках"
                            >
                                <FaIcon className="fas fa-shuffle" /> Случайный звонок
                            </button>
                        )}
                        {viewMode === 'normal' && isRandomChatDirection && (
                            <button
                                className={`btn btn-secondary btn-sm ${!selectedOperator ? 'disabled' : ''}`}
                                style={{opacity:!selectedOperator?0.4:1,cursor:!selectedOperator?'not-allowed':'pointer'}}
                                onClick={() => { if (!selectedOperator) return; setShowRandomChatModal(true); }}
                                disabled={!selectedOperator}
                                title={randomChatSource === 'chat2desk'
                                    ? 'Взять случайный чат из Chat2Desk по фильтрам (период, длина, оценка клиента) и оценить его по критериям направления'
                                    : `Взять случайный эпизод переписки ${CHAT_SOURCE_META[randomChatSource]?.label || 'WhatsApp'} по фильтрам (период, длина) и оценить его по критериям направления`}
                            >
                                <FaIcon className="fas fa-comments" /> Случайный чат
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
                ) : activeSection === 'requests' ? (
                    <>
                        <div className="stats-bar">
                            <div className="stat-item">
                                <div className="stat-icon blue"><FaIcon className="fas fa-list-check" /></div>
                                <div>
                                    <div className="stat-value">{requestStats.total}</div>
                                    <div className="stat-label">Всего запросов</div>
                                </div>
                            </div>
                            <div className="stat-item">
                                <div className="stat-icon" style={{ background: 'var(--amber-light)', color: 'var(--amber)' }}><FaIcon className="fas fa-hourglass-half" /></div>
                                <div>
                                    <div className="stat-value" style={{ color: 'var(--amber)' }}>{requestStats.pending}</div>
                                    <div className="stat-label">На рассмотрении</div>
                                </div>
                            </div>
                            <div className="stat-item">
                                <div className="stat-icon green"><FaIcon className="fas fa-check-double" /></div>
                                <div>
                                    <div className="stat-value" style={{ color: 'var(--green)' }}>{requestStats.approved + requestStats.completed}</div>
                                    <div className="stat-label">Одобрено</div>
                                </div>
                            </div>
                            <div className="stat-item">
                                <div className="stat-icon" style={{ background: 'var(--accent-light)', color: 'var(--accent)' }}><FaIcon className="fas fa-redo" /></div>
                                <div>
                                    <div className="stat-value" style={{ color: 'var(--accent)' }}>{requestStats.completed}</div>
                                    <div className="stat-label">Переоценено</div>
                                </div>
                            </div>
                            <div className="stat-item">
                                <div className="stat-icon" style={{ background: 'var(--red-light)', color: 'var(--red)' }}><FaIcon className="fas fa-ban" /></div>
                                <div>
                                    <div className="stat-value" style={{ color: 'var(--red)' }}>{requestStats.rejected}</div>
                                    <div className="stat-label">Отклонено</div>
                                </div>
                            </div>
                        </div>

                        <div className="table-wrap">
                            {isRequestsLoading ? (
                                <table>
                                    <thead>
                                        <tr>
                                            <th>#</th>
                                            <th>Статус</th>
                                            <th>Оператор</th>
                                            <th>Инициатор</th>
                                            <th>Телефон / Направление</th>
                                            <th>Дата запроса</th>
                                            <th>Итог / Действия</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {[...Array(5)].map((_, i) => (
                                            <tr key={i}>
                                                <td colSpan={7}>
                                                    <div style={{ display: 'grid', gridTemplateColumns: '40px 120px 1fr 1fr 180px 160px 1fr', gap: 8, padding: '12px 16px 12px 20px' }}>
                                                        {[40, 120, '1fr', '1fr', 180, 160, '1fr'].map((w, j) => (
                                                            <div key={j} className="skeleton" style={{ height: 16, width: typeof w === 'number' ? w : '100%' }} />
                                                        ))}
                                                    </div>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            ) : filteredReevaluationRequests.length === 0 ? (
                                <div className="empty-state">
                                    <div className="empty-state-icon"><FaIcon className="fas fa-clipboard-check" /></div>
                                    <h3>{reevaluationSearchNormalized ? 'Ничего не найдено' : 'Нет запросов'}</h3>
                                    <p>
                                        {reevaluationSearchNormalized
                                            ? 'Попробуйте изменить запрос поиска.'
                                            : 'За выбранный месяц запросы на переоценку не найдены.'}
                                    </p>
                                </div>
                            ) : (
                                <table>
                                    <thead>
                                        <tr>
                                            <th>#</th>
                                            <th>Статус</th>
                                            <th>Оператор</th>
                                            <th>Инициатор</th>
                                            <th>Телефон / Направление</th>
                                            <th>Дата запроса</th>
                                            <th>Итог / Действия</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {filteredReevaluationRequests.map((requestItem, idx) => {
                                            const statusMeta = getReevaluationRequestStatusMeta(requestItem);
                                            const outcomeMeta = getReevaluationRequestOutcomeMeta(requestItem);
                                            const statusToneStyle = statusMeta.status === 'approved'
                                                ? { background: 'var(--green-light)', color: 'var(--green)' }
                                                : statusMeta.status === 'rejected'
                                                    ? { background: 'var(--red-light)', color: 'var(--red)' }
                                                    : statusMeta.status === 'pending'
                                                        ? { background: 'var(--amber-light)', color: 'var(--amber)' }
                                                        : { background: 'var(--surface-2)', color: 'var(--text-2)' };
                                            const outcomeToneStyle = outcomeMeta.label === 'Переоценено'
                                                ? { background: 'var(--accent-light)', color: 'var(--accent)' }
                                                : statusToneStyle;
                                            return (
                                                <React.Fragment key={requestItem.id}>
                                                    <tr
                                                        className={`clickable ${expandedId === requestItem.id ? 'expanded' : ''}`}
                                                        onClick={() => setExpandedId(expandedId === requestItem.id ? null : requestItem.id)}
                                                    >
                                                        <td>{idx + 1}</td>
                                                        <td>
                                                            <span className="badge" style={statusToneStyle}>
                                                                <span className="badge-dot" />
                                                                {statusMeta.label}
                                                            </span>
                                                        </td>
                                                        <td>
                                                            <div style={{ fontWeight: 600 }}>{requestItem.operator_name || '—'}</div>
                                                            <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
                                                                СВ: {requestItem.supervisor_name || '—'}
                                                            </div>
                                                        </td>
                                                        <td>
                                                            <div style={{ fontWeight: 600 }}>{requestItem.sv_request_by_name || '—'}</div>
                                                            <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
                                                                {getReevaluationRequestRoleLabel(requestItem.sv_request_by_role)}
                                                            </div>
                                                        </td>
                                                        <td>
                                                            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{requestItem.phone_number || '—'}</div>
                                                            <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
                                                                {requestItem.direction?.name || '—'}
                                                            </div>
                                                        </td>
                                                        <td style={{ fontSize: 12, color: 'var(--text-2)' }}>{fmtDate(requestItem.sv_request_at)}</td>
                                                        <td>
                                                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                                                                <span className="badge" style={outcomeToneStyle}>
                                                                    <span className="badge-dot" />
                                                                    {outcomeMeta.label}
                                                                </span>
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                                                                    {canDecideReevaluationRequests && statusMeta.status === 'pending' ? (
                                                                        <SvRequestButton
                                                                            call={requestItem}
                                                                            userId={userId}
                                                                            userRole={userRole}
                                                                            isAdminRole={canDecideReevaluationRequests}
                                                                            fetchEvaluations={selectedOperator?.id ? fetchEvaluations : undefined}
                                                                            onUpdated={handleRequestsUpdated}
                                                                        />
                                                                    ) : null}
                                                                    <button
                                                                        className="btn btn-secondary btn-sm"
                                                                        onClick={() => openRequestInJournal(requestItem)}
                                                                    >
                                                                        К журналу
                                                                    </button>
                                                                </div>
                                                            </div>
                                                        </td>
                                                    </tr>

                                                    {expandedId === requestItem.id && (
                                                        <tr className="expanded-row">
                                                            <td colSpan={7}>
                                                                <div className="expanded-content">
                                                                    <h4>Детали запроса</h4>
                                                                    <div className="expanded-meta">
                                                                        <div className="expanded-meta-item"><strong>Оператор:</strong> {requestItem.operator_name || '—'}</div>
                                                                        <div className="expanded-meta-item"><strong>Супервайзер:</strong> {requestItem.supervisor_name || '—'}</div>
                                                                        <div className="expanded-meta-item"><strong>Инициатор:</strong> {requestItem.sv_request_by_name || '—'} ({getReevaluationRequestRoleLabel(requestItem.sv_request_by_role)})</div>
                                                                        <div className="expanded-meta-item"><strong>Дата запроса:</strong> {fmtDate(requestItem.sv_request_at)}</div>
                                                                        <div className="expanded-meta-item"><strong>Телефон:</strong> {requestItem.phone_number || '—'}</div>
                                                                        <div className="expanded-meta-item"><strong>Направление:</strong> {requestItem.direction?.name || '—'}</div>
                                                                        <div className="expanded-meta-item"><strong>Исходный балл:</strong> {requestItem.score != null ? Math.round(requestItem.score) : '—'}</div>
                                                                        <div className="expanded-meta-item"><strong>Оценщик:</strong> {requestItem.evaluator || '—'}</div>
                                                                    </div>

                                                                    <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--text-2)', padding: '10px 12px', border: `1px solid ${statusMeta.color}`, borderRadius: 'var(--radius)', background: 'var(--surface)' }}>
                                                                        <div style={{ marginBottom: 6 }}>
                                                                            <strong style={{ color: 'var(--text)' }}>Статус запроса:</strong>{' '}
                                                                            <span style={{ color: statusMeta.color, fontWeight: 600 }}>{statusMeta.label}</span>
                                                                        </div>
                                                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', marginBottom: (requestItem.sv_request_comment || requestItem.sv_request_reject_comment || requestItem.sv_request_approve_comment) ? 6 : 0 }}>
                                                                            {requestItem.sv_request_approved_by_name ? <span><strong style={{ color: 'var(--text)' }}>Одобрил:</strong> {requestItem.sv_request_approved_by_name}{requestItem.sv_request_approved_at ? ` · ${requestItem.sv_request_approved_at}` : ''}</span> : null}
                                                                            {requestItem.sv_request_rejected_by_name ? <span><strong style={{ color: 'var(--text)' }}>Отклонил:</strong> {requestItem.sv_request_rejected_by_name}{requestItem.sv_request_rejected_at ? ` · ${requestItem.sv_request_rejected_at}` : ''}</span> : null}
                                                                        </div>
                                                                        {requestItem.sv_request_comment ? (
                                                                            <div style={{ marginBottom: (requestItem.sv_request_reject_comment || requestItem.sv_request_approve_comment) ? 4 : 0 }}>
                                                                                <strong style={{ color: 'var(--text)' }}>Комментарий к запросу:</strong> {requestItem.sv_request_comment}
                                                                            </div>
                                                                        ) : null}
                                                                        {requestItem.sv_request_approved && requestItem.sv_request_approve_comment ? (
                                                                            <div>
                                                                                <strong style={{ color: 'var(--text)' }}>Комментарий при одобрении:</strong> {requestItem.sv_request_approve_comment}
                                                                            </div>
                                                                        ) : null}
                                                                        {requestItem.sv_request_reject_comment ? (
                                                                            <div>
                                                                                <strong style={{ color: 'var(--text)' }}>Причина отклонения:</strong> {requestItem.sv_request_reject_comment}
                                                                            </div>
                                                                        ) : null}
                                                                    </div>

                                                                    {requestItem.correction_call_id ? (
                                                                        <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--text-2)', padding: '10px 12px', border: '1px solid var(--accent)', borderRadius: 'var(--radius)', background: 'var(--accent-light)' }}>
                                                                            <div style={{ marginBottom: 6 }}>
                                                                                <strong style={{ color: 'var(--text)' }}>Итог переоценки:</strong>{' '}
                                                                                <span style={{ color: 'var(--accent)', fontWeight: 600 }}>Переоценка выполнена</span>
                                                                            </div>
                                                                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px' }}>
                                                                                <span><strong style={{ color: 'var(--text)' }}>Новый Call ID:</strong> {requestItem.correction_call_id}</span>
                                                                                <span><strong style={{ color: 'var(--text)' }}>Новый балл:</strong> {requestItem.correction_score != null ? Math.round(requestItem.correction_score) : '—'}</span>
                                                                                <span><strong style={{ color: 'var(--text)' }}>Переоценил:</strong> {requestItem.correction_evaluator_name || '—'}</span>
                                                                                <span><strong style={{ color: 'var(--text)' }}>Дата:</strong> {fmtDate(requestItem.correction_created_at)}</span>
                                                                            </div>
                                                                        </div>
                                                                    ) : requestItem.sv_request_approved ? (
                                                                        <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--text-2)', padding: '10px 12px', border: '1px dashed var(--border-strong)', borderRadius: 'var(--radius)', background: 'var(--surface-2)' }}>
                                                                            Запрос одобрен, но переоценка ещё не выполнена.
                                                                        </div>
                                                                    ) : null}
                                                                </div>
                                                            </td>
                                                        </tr>
                                                    )}
                                                </React.Fragment>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            )}
                        </div>

                        <div className="panel-footer">
                            <span className="panel-footer-info">
                                {requestFooterInfo || 'Нет данных для отображения'}
                            </span>
                            <div style={{ display: 'flex', gap: 8 }} />
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
                                                    {isGlobalAdminRole && (
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
                                                                    {isGlobalAdminRole && (
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
            )}

            {canManageEvaluationNotifications && activeSection === 'journal' && (
                <button
                    type="button"
                    className={`notification-settings-fab ${hasEnabledJournalNotifications ? 'has-active-subscription' : ''}`}
                    onClick={() => setShowNotificationSettings(true)}
                    aria-label="Открыть настройки уведомлений"
                    aria-haspopup="dialog"
                    aria-expanded={showNotificationSettings}
                    title="Настройки уведомлений"
                >
                    <span className="notification-settings-fab-icon" aria-hidden="true">
                        <FaIcon className="fas fa-bell" />
                    </span>
                    <span className="notification-settings-fab-label">Уведомления</span>
                    {hasEnabledJournalNotifications && <span className="notification-settings-fab-dot" aria-hidden="true" />}
                </button>
            )}

            {showNotificationSettings && canManageEvaluationNotifications && activeSection === 'journal' && (
                <div
                    className="modal-backdrop notification-settings-backdrop"
                    role="presentation"
                    onMouseDown={(event) => {
                        if (event.target === event.currentTarget) setShowNotificationSettings(false);
                    }}
                >
                    <div
                        className="modal notification-settings-modal"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="notification-settings-title"
                        onMouseDown={(event) => event.stopPropagation()}
                    >
                        <div className="modal-header notification-settings-modal-header">
                            <div className="notification-settings-heading">
                                <span className="notification-settings-heading-icon" aria-hidden="true">
                                    <FaIcon className="fas fa-bell" />
                                </span>
                                <div>
                                    <h2 id="notification-settings-title">Уведомления Журнала оценок</h2>
                                    <div className="modal-header-sub">Персональные подписки в Telegram</div>
                                </div>
                            </div>
                            <button
                                type="button"
                                className="close-btn"
                                onClick={() => setShowNotificationSettings(false)}
                                aria-label="Закрыть настройки уведомлений"
                            >
                                <FaIcon className="fas fa-times" />
                            </button>
                        </div>
                        <div className="modal-body notification-settings-body">
                            <div className="notification-settings-intro">
                                <FaIcon className="fas fa-info-circle" />
                                <p>
                                    Выберите, какие события получать лично. Настройки действуют только для вашей учётной записи,
                                    а сообщения приходят в Telegram, привязанный к профилю.
                                </p>
                            </div>

                            <section className="notification-subscription-card" aria-labelledby="evaluation-notification-title">
                                <div className="notification-subscription-main">
                                    <div className="notification-subscription-icon notification-subscription-icon-blue" aria-hidden="true">
                                        <FaIcon className="fas fa-clipboard-check" />
                                    </div>
                                    <div className="notification-subscription-copy">
                                        <div className="notification-subscription-title-row">
                                            <h3 id="evaluation-notification-title">Новые оценки и переоценки</h3>
                                            <span className="notification-subscription-badge">Сразу после публикации</span>
                                        </div>
                                        <p>
                                            Telegram сообщит о каждой опубликованной оценке сотрудника выбранного отдела.
                                            В сообщении будут оценщик, оператор, месяц, номер обращения, итоговый балл,
                                            комментарий и запись звонка, если она доступна.
                                        </p>
                                    </div>
                                    <label className="notification-settings-switch">
                                        <input
                                            type="checkbox"
                                            role="switch"
                                            aria-label="Получать уведомления о новых оценках и переоценках"
                                            checked={!!evaluationNotifySetting.enabled}
                                            disabled={evaluationNotifyDisabled}
                                            onChange={(event) => saveEvaluationNotifySetting({
                                                enabled: event.target.checked,
                                                departmentId: evaluationNotifySetting.departmentId
                                            })}
                                        />
                                        <span className="notification-settings-switch-track" aria-hidden="true">
                                            <span className="notification-settings-switch-thumb" />
                                        </span>
                                    </label>
                                </div>

                                <div className="notification-subscription-options evaluation-notify-control">
                                    {evaluationNotifySetting.scope === 'admin' ? (
                                        <div className="notification-settings-field">
                                            <label className="label" htmlFor="evaluation-notify-department">Отдел для подписки</label>
                                            <select
                                                id="evaluation-notify-department"
                                                className="select evaluation-notify-select"
                                                value={evaluationNotifySetting.departmentId || ''}
                                                disabled={
                                                    !!evaluationNotifySetting.loading ||
                                                    !!evaluationNotifySetting.saving ||
                                                    !evaluationNotifySetting.telegramConnected
                                                }
                                                onChange={(event) => {
                                                    const nextDepartmentId = event.target.value;
                                                    setEvaluationNotifySetting(prev => ({
                                                        ...prev,
                                                        departmentId: nextDepartmentId,
                                                        departmentName: evaluationNotifyDepartments.find(
                                                            (dept) => String(dept?.id) === String(nextDepartmentId)
                                                        )?.name || ''
                                                    }));
                                                    if (evaluationNotifySetting.enabled) {
                                                        saveEvaluationNotifySetting({ enabled: true, departmentId: nextDepartmentId });
                                                    }
                                                }}
                                            >
                                                <option value="" disabled={!!evaluationNotifySetting.enabled}>Выберите отдел</option>
                                                {evaluationNotifyDepartments.map((dept) => (
                                                    <option key={dept.id} value={dept.id}>{dept.name}</option>
                                                ))}
                                            </select>
                                        </div>
                                    ) : (
                                        <div className="notification-settings-scope">
                                            <span className="notification-settings-scope-label">Область подписки</span>
                                            <span className="notification-settings-scope-value">
                                                <FaIcon className="fas fa-building" />
                                                {evaluationNotifyDepartmentName || 'Ваш отдел'}
                                            </span>
                                        </div>
                                    )}
                                    <div className={`notification-settings-status ${evaluationNotifySetting.telegramConnected ? 'is-connected' : 'is-warning'}`}>
                                        <FaIcon className={`fas fa-${evaluationNotifySetting.telegramConnected ? 'paper-plane' : 'exclamation-circle'}`} />
                                        {evaluationNotifySetting.loading
                                            ? 'Загрузка настройки...'
                                            : (evaluationNotifySetting.saving
                                                ? 'Сохраняем...'
                                                : (evaluationNotifySetting.telegramConnected
                                                    ? (evaluationNotifySetting.enabled ? 'Подписка включена' : 'Подписка выключена')
                                                    : 'Telegram не подключён к профилю'))}
                                    </div>
                                </div>
                            </section>

                            {canManageFeedbackReportSetting && (
                                <section className="notification-subscription-card" aria-labelledby="feedback-report-title">
                                    <div className="notification-subscription-main">
                                        <div className="notification-subscription-icon notification-subscription-icon-green" aria-hidden="true">
                                            <FaIcon className="fas fa-chart-line" />
                                        </div>
                                        <div className="notification-subscription-copy">
                                            <div className="notification-subscription-title-row">
                                                <h3 id="feedback-report-title">Еженедельный отчёт по обратной связи</h3>
                                                <span className="notification-subscription-badge">Понедельник · 09:05</span>
                                            </div>
                                            <p>
                                                В Telegram придёт оформленный Excel-файл за текущий месяц: сводка {feedbackReportSetting.scope === 'department'
                                                    ? `по отделу «${feedbackReportSetting.departmentName || 'Ваш отдел'}»`
                                                    : 'по всем отделам'},
                                                показатели соблюдения сроков, диаграмма по супервайзерам и полная детализация
                                                по каждой оценке.
                                            </p>
                                        </div>
                                        <label className="notification-settings-switch">
                                            <input
                                                type="checkbox"
                                                role="switch"
                                                aria-label="Получать еженедельный отчёт по обратной связи"
                                                checked={!!feedbackReportSetting.enabled}
                                                disabled={
                                                    !!feedbackReportSetting.loading ||
                                                    !!feedbackReportSetting.saving ||
                                                    !feedbackReportSetting.telegramConnected
                                                }
                                                onChange={(event) => toggleFeedbackReportSetting(event.target.checked)}
                                            />
                                            <span className="notification-settings-switch-track" aria-hidden="true">
                                                <span className="notification-settings-switch-thumb" />
                                            </span>
                                        </label>
                                    </div>
                                    <div className="notification-subscription-options">
                                        <div className="notification-settings-scope">
                                            <span className="notification-settings-scope-label">Период и охват</span>
                                            <span className="notification-settings-scope-value">
                                                <FaIcon className={`fas fa-${feedbackReportSetting.scope === 'department' ? 'building' : 'globe'}`} />
                                                Текущий месяц · {feedbackReportSetting.scope === 'department'
                                                    ? (feedbackReportSetting.departmentName || 'ваш отдел')
                                                    : 'все отделы'}
                                            </span>
                                        </div>
                                        <div className={`notification-settings-status ${feedbackReportSetting.telegramConnected ? 'is-connected' : 'is-warning'}`}>
                                            <FaIcon className={`fas fa-${feedbackReportSetting.telegramConnected ? 'paper-plane' : 'exclamation-circle'}`} />
                                            {feedbackReportSetting.loading
                                                ? 'Загрузка настройки...'
                                                : (feedbackReportSetting.saving
                                                    ? 'Сохраняем...'
                                                    : (feedbackReportSetting.telegramConnected
                                                        ? (feedbackReportSetting.enabled ? 'Подписка включена' : 'Подписка выключена')
                                                        : 'Telegram не подключён к профилю'))}
                                        </div>
                                    </div>
                                    <div className="notification-report-preview">
                                        <div className="notification-report-preview-copy">
                                            <span className="notification-report-preview-icon" aria-hidden="true">
                                                <FaIcon className="fas fa-file-excel" />
                                            </span>
                                            <span>
                                                <strong>Посмотреть отчёт сейчас</strong>
                                                <small>Разовая отправка не включает еженедельную подписку.</small>
                                            </span>
                                        </div>
                                        <button
                                            type="button"
                                            className="btn btn-secondary btn-sm"
                                            onClick={sendFeedbackReportPreview}
                                            disabled={
                                                feedbackReportPreviewSending ||
                                                feedbackReportSetting.loading ||
                                                !feedbackReportSetting.telegramConnected
                                            }
                                        >
                                            {feedbackReportPreviewSending
                                                ? <><span className="spinner spinner-dark" /> Формируем...</>
                                                : <><FaIcon className="fab fa-telegram-plane" /> Отправить Excel</>}
                                        </button>
                                    </div>
                                </section>
                            )}

                            {!evaluationNotifySetting.telegramConnected && (
                                <div className="notification-settings-telegram-note">
                                    <FaIcon className="fab fa-telegram-plane" />
                                    <div>
                                        <strong>Сначала подключите Telegram</strong>
                                        <span>После привязки Telegram к профилю тумблеры станут доступны.</span>
                                    </div>
                                </div>
                            )}
                        </div>
                        <div className="modal-footer notification-settings-footer">
                            <span>Изменения в подписках сохраняются автоматически.</span>
                            <button type="button" className="btn btn-primary" onClick={() => setShowNotificationSettings(false)}>
                                Готово
                            </button>
                        </div>
                    </div>
                </div>
            )}

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
            <RandomCallModal
                isOpen={showRandomModal}
                onClose={() => setShowRandomModal(false)}
                operator={selectedOperator}
                userId={userId}
                selectedMonth={selectedMonth}
                source={selectedOperatorDirectionMeta?.random_call_source || null}
                onImported={handleRandomCallsImported}
            />
            <RandomChatModal
                isOpen={showRandomChatModal}
                onClose={() => setShowRandomChatModal(false)}
                operator={selectedOperator}
                userId={userId}
                selectedMonth={selectedMonth}
                source={randomChatSource}
                onPicked={(data) => setChatEvalData(data)}
            />
            <ChatEvaluationModal
                isOpen={!!chatEvalData}
                onClose={() => setChatEvalData(null)}
                operator={selectedOperator}
                chatData={chatEvalData}
                directions={directions}
                selectedMonth={selectedMonth}
                userId={userId}
                userName={userName}
                onSubmitted={() => fetchEvaluations({ force: true })}
            />
            <ChatViewModal
                isOpen={!!chatViewTarget}
                onClose={() => setChatViewTarget(null)}
                snapshotId={chatViewTarget?.snapshotId}
                quotes={chatViewTarget?.quotes}
                title={chatViewTarget?.title}
                userId={userId}
            />
            <FeedbackModal
                isOpen={showFeedbackModal}
                onClose={() => { setShowFeedbackModal(false); setFeedbackTargetCall(null); }}
                call={feedbackTargetCall}
                userId={userId}
                onSaved={handleFeedbackSaved}
            />
            <BatchFeedbackModal
                isOpen={showBatchFeedbackModal}
                onClose={() => setShowBatchFeedbackModal(false)}
                calls={batchModalCalls}
                userId={userId}
                onSaved={handleBatchFeedbackSaved}
            />
            {batchMode && !showBatchFeedbackModal && (
                <div style={{
                    position:'fixed', left:'50%', bottom:24, transform:'translateX(-50%)',
                    zIndex:50, display:'flex', alignItems:'center', gap:12,
                    padding:'10px 16px', borderRadius:'var(--radius)',
                    background:'var(--surface)', border:'1px solid var(--border-strong)',
                    boxShadow:'0 8px 24px rgba(0,0,0,0.18)'
                }}>
                    <span style={{fontSize:13, color:'var(--text)'}}>Выбор ОС · выбрано: <strong>{selectedBatchCalls.length}</strong></span>
                    <button className="btn btn-sm btn-secondary" onClick={exitBatchMode}>Отмена</button>
                    <button
                        className="btn btn-sm btn-primary"
                        disabled={selectedBatchCalls.length === 0}
                        onClick={() => { setBatchModalCalls(selectedBatchCalls); setShowBatchFeedbackModal(true); }}
                    >
                        <FaIcon className="fas fa-comments" /> Дать ОС{selectedBatchCalls.length > 0 ? ` (${selectedBatchCalls.length})` : ''}
                    </button>
                </div>
            )}
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

            {analyticsReportModal.show && (
                <div
                    className="modal-backdrop"
                    onMouseDown={(e) => {
                        if (e.target === e.currentTarget) closeAnalyticsReportModal();
                    }}
                >
                    <div className="modal analytics-export-modal" onMouseDown={(e) => e.stopPropagation()}>
                        <div className="modal-header">
                            <div>
                                <h2>Выгрузка отчёта</h2>
                                <div className="modal-header-sub">Журнал оценок за выбранный месяц</div>
                            </div>
                            <button
                                type="button"
                                className="close-btn"
                                onClick={closeAnalyticsReportModal}
                                disabled={analyticsLoading}
                                title="Закрыть"
                            >
                                <FaIcon className="fas fa-times" />
                            </button>
                        </div>
                        <div className="modal-body analytics-export-body">
                            <div className="analytics-export-grid">
                                <div className="field">
                                    <label className="label">Месяц</label>
                                    <select
                                        value={analyticsMonth}
                                        onChange={(e) => setAnalyticsMonth(e.target.value)}
                                        className="select"
                                        disabled={analyticsLoading}
                                    >
                                        {getAnalyticsMonthOptions()}
                                    </select>
                                </div>
                                {isGlobalAdminRole && (
                                    <div className="field">
                                        <label className="label">Отдел</label>
                                        <select
                                            value={analyticsReportModal.departmentId}
                                            onChange={(e) => setAnalyticsReportModal(prev => ({
                                                ...prev,
                                                departmentId: e.target.value
                                            }))}
                                            className="select"
                                            disabled={analyticsLoading || analyticsReportDepartmentsLoading}
                                        >
                                            <option value="">Все отделы</option>
                                            {analyticsReportDepartments.map((dept) => (
                                                <option key={dept.id} value={dept.id}>{dept.name}</option>
                                            ))}
                                        </select>
                                    </div>
                                )}
                            </div>

                            {!isGlobalAdminRole && (
                                <div className="analytics-export-scope-note">
                                    <FaIcon className="fas fa-building" />
                                    <span>Данные будут выгружены по отделу сотрудника.</span>
                                </div>
                            )}

                            <div className="field" style={{ marginBottom: 0 }}>
                                <label className="label">Формат</label>
                                <div className="analytics-export-format-list">
                                    <button
                                        type="button"
                                        className={`analytics-export-format ${analyticsReportModal.format === 'standard' ? 'active' : ''}`}
                                        onClick={() => setAnalyticsReportModal(prev => ({ ...prev, format: 'standard' }))}
                                        disabled={analyticsLoading}
                                    >
                                        <span className="analytics-export-format-icon"><FaIcon className="fas fa-table" /></span>
                                        <span className="analytics-export-format-text">
                                            <span className="analytics-export-format-title">Обычный с количеством</span>
                                            <span className="analytics-export-format-desc">Оценки подряд, средний балл, кол-во и план.</span>
                                        </span>
                                    </button>
                                    <button
                                        type="button"
                                        className={`analytics-export-format ${analyticsReportModal.format === 'dates' ? 'active' : ''}`}
                                        onClick={() => setAnalyticsReportModal(prev => ({ ...prev, format: 'dates' }))}
                                        disabled={analyticsLoading}
                                    >
                                        <span className="analytics-export-format-icon"><FaIcon className="fas fa-calendar-alt" /></span>
                                        <span className="analytics-export-format-text">
                                            <span className="analytics-export-format-title">По датам</span>
                                            <span className="analytics-export-format-desc">ФИО и дни месяца, несколько оценок в день через запятую.</span>
                                        </span>
                                    </button>
                                    <button
                                        type="button"
                                        className={`analytics-export-format ${analyticsReportModal.format === 'group' ? 'active' : ''}`}
                                        onClick={() => setAnalyticsReportModal(prev => ({ ...prev, format: 'group' }))}
                                        disabled={analyticsLoading}
                                    >
                                        <span className="analytics-export-format-icon"><FaIcon className="fas fa-users" /></span>
                                        <span className="analytics-export-format-text">
                                            <span className="analytics-export-format-title">По группе (по листам)</span>
                                            <span className="analytics-export-format-desc">Отдельный лист на каждого оператора выбранного СВ — как в Журнале.</span>
                                        </span>
                                    </button>
                                </div>
                            </div>

                            {analyticsReportModal.format === 'group' && (
                                <div className="field" style={{ marginTop: 12, marginBottom: 0 }}>
                                    <label className="label">Супервайзер (группа)</label>
                                    <select
                                        value={analyticsReportModal.supervisorId}
                                        onChange={(e) => setAnalyticsReportModal(prev => ({
                                            ...prev,
                                            supervisorId: e.target.value
                                        }))}
                                        className="select"
                                        disabled={analyticsLoading}
                                    >
                                        <option value="">Выберите супервайзера</option>
                                        {orderedSupervisors
                                            .filter(sv => {
                                                if (!isGlobalAdminRole) return true;
                                                const deptId = analyticsReportModal.departmentId;
                                                if (!deptId) return true;
                                                if (sv.department_id == null) return true;
                                                return String(sv.department_id) === String(deptId);
                                            })
                                            .map(sv => (
                                                <option
                                                    key={sv.id}
                                                    value={sv.id}
                                                    className={isFiredStatus(sv?.status) ? 'option-fired' : ''}
                                                    style={isFiredStatus(sv?.status) ? { color: 'var(--text-3)' } : undefined}
                                                >
                                                    {sv.name}
                                                </option>
                                            ))}
                                    </select>
                                    <div className="analytics-export-scope-note" style={{ marginTop: 8 }}>
                                        <FaIcon className="fas fa-layer-group" />
                                        <span>На каждого оператора группы — отдельный лист с журналом оценок за месяц.</span>
                                    </div>
                                </div>
                            )}
                        </div>
                        <div className="modal-footer">
                            <button className="btn btn-secondary" onClick={closeAnalyticsReportModal} disabled={analyticsLoading}>Отмена</button>
                            <button
                                className="btn btn-primary"
                                onClick={() => analyticsGenerateReport({
                                    confirmed: true,
                                    format: analyticsReportModal.format,
                                    departmentId: analyticsReportModal.departmentId,
                                    supervisorId: analyticsReportModal.supervisorId
                                })}
                                disabled={analyticsLoading || (analyticsReportModal.format === 'group' && !analyticsReportModal.supervisorId)}
                            >
                                {analyticsLoading ? <><span className="spinner" /> Выгрузка...</> : <><FaIcon className="fas fa-download" /> Скачать</>}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* ── Analytics section ── */}
            {activeSection === 'analytics' && canUseAnalytics && (
                <div className="main-panel" style={{ margin: '0 0 16px' }}>
                    <div className="panel-header">
                        <div className="panel-title-wrap">
                            <span className="panel-title">Аналитика</span>
                            <div className="filter-group" style={{ marginBottom: 0, minWidth: 220 }}>
                                <label className="label">Супервайзер</label>
                                <select
                                    className="select"
                                    value={analyticsSelectedSvId}
                                    style={analyticsSelectedSupervisorIsFired ? { color:'var(--text-3)' } : undefined}
                                    onChange={(e) => {
                                        setAnalyticsSelectedSvId(e.target.value);
                                        setAnalyticsSelectedSvData(null);
                                    }}
                                    disabled={analyticsLoading}
                                >
                                    <option value="">Выбрать</option>
                                    {orderedSupervisors.map(sv => (
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
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            <select
                                value={analyticsMonth}
                                onChange={(e) => setAnalyticsMonth(e.target.value)}
                                className="select"
                                disabled={analyticsLoading}
                            >
                                {getAnalyticsMonthOptions()}
                            </select>
                            <button
                                className="btn btn-sm"
                                style={{ background: 'var(--green-light)', color: 'var(--green)', borderColor: 'var(--green)' }}
                                onClick={openAnalyticsReportModal}
                                disabled={analyticsLoading}
                                title="Скачать отчёт по прослушанным звонкам за выбранный месяц"
                            >
                                <FaIcon className="fas fa-file-excel" /> {analyticsLoading ? 'Загрузка...' : 'Скачать отчёт'}
                            </button>
                            <button
                                className="btn btn-sm btn-primary"
                                onClick={() => { if (!analyticsEffectiveSvId) { emitCallEvaluationToast('Выберите супервайзера', 'error'); return; } fetchAnalyticsSvData(analyticsEffectiveSvId, analyticsMonth); }}
                                disabled={analyticsLoading || !analyticsEffectiveSvId}
                            >
                                <FaIcon className="fas fa-sync-alt" /> {analyticsLoading ? 'Обновление...' : 'Обновить'}
                            </button>
                        </div>
                    </div>

                    <div style={{ padding: '16px 24px' }}>
                        {/* Active / Fired tabs */}
                        <div className="section-switch" style={{ marginBottom: 14 }}>
                            {(() => {
                                const all = analyticsScopedOperators;
                                const activeCount = all.filter(op => !isFiredStatus(op.status) || hasAnyEvaluationIndicators(op)).length;
                                const firedCount = all.filter(op => isFiredStatus(op.status) && !hasAnyEvaluationIndicators(op)).length;
                                return (
                                    <>
                                        <button className={`btn btn-sm ${analyticsActiveOperatorsTab === 'active' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setAnalyticsActiveOperatorsTab('active')}>
                                            Активные ({activeCount})
                                        </button>
                                        <button className={`btn btn-sm ${analyticsActiveOperatorsTab === 'fired' ? '' : 'btn-secondary'}`} style={analyticsActiveOperatorsTab === 'fired' ? { background: 'var(--red)', color: '#fff', borderColor: 'var(--red)' } : {}} onClick={() => setAnalyticsActiveOperatorsTab('fired')}>
                                            Уволенные ({firedCount})
                                        </button>
                                    </>
                                );
                            })()}
                        </div>

                        {/* Table */}
                        {analyticsLoading ? (
                            <p style={{ textAlign: 'center', color: 'var(--text-2)', padding: '32px 0', fontSize: 13 }}>Загрузка...</p>
                        ) : analyticsScopedOperators.length > 0 ? (() => {
                            const allOps = analyticsScopedOperators;
                            const filteredOps = analyticsActiveOperatorsTab === 'active'
                                ? allOps.filter(op => !isFiredStatus(op.status) || hasAnyEvaluationIndicators(op))
                                : allOps.filter(op => isFiredStatus(op.status) && !hasAnyEvaluationIndicators(op));

                            if (filteredOps.length === 0) return <p style={{ textAlign: 'center', color: 'var(--text-2)', padding: '32px 0', fontSize: 13 }}>Операторы не найдены.</p>;

                            const sorted = [...filteredOps].sort(compareAnalyticsByField);

                            const totalCalls = filteredOps.reduce((s, o) => s + (parseInt(o.call_count) || 0), 0);
                            const totalPlanCalls = filteredOps.reduce((s, o) => { const m = getAnalyticsEvaluationPlanMeta(o); return s + (m ? m.requiredCalls : 0); }, 0);
                            const scoresArr = filteredOps.map(o => o.avg_score == null ? NaN : Number(o.avg_score)).filter(v => !isNaN(v));
                            const avgByScored = scoresArr.length > 0 ? scoresArr.reduce((a, b) => a + b, 0) / scoresArr.length : null;

                            const thStyle = { padding: '10px 12px', textAlign: 'left', fontSize: 11, fontWeight: 600, color: 'var(--text-2)', textTransform: 'uppercase', letterSpacing: '0.04em', cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' };
                            const tdStyle = { padding: '10px 12px', fontSize: 13, color: 'var(--text)', borderTop: '1px solid var(--border)' };

                            const getScoreColor = (score) => { if (!score) return 'var(--text-2)'; if (score >= 90) return 'var(--green)'; if (score >= 60) return 'var(--amber)'; return 'var(--red)'; };
                            const getBarColor = (pct) => getAnalyticsNormTone(pct).color;

                            return (
                                <div style={{ borderRadius: 8, border: '1px solid var(--border)' }}>
                                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                        <thead style={{ background: 'var(--surface-2)' }}>
                                            <tr>
                                                <th style={thStyle} onClick={() => handleAnalyticsSort('name')}>Оператор {getAnalyticsSortIcon('name')}</th>
                                                <th style={thStyle} onClick={() => handleAnalyticsSort('listened')}>Оценено / план {getAnalyticsSortIcon('listened')}</th>
                                                <th style={thStyle} onClick={() => handleAnalyticsSort('percent')}>% нормы {getAnalyticsSortIcon('percent')}</th>
                                                <th style={thStyle} onClick={() => handleAnalyticsSort('avg_score')}>Ср. балл {getAnalyticsSortIcon('avg_score')}</th>
                                                <th style={thStyle} onClick={() => handleAnalyticsSort('feedback')}>ОС / просрочки {getAnalyticsSortIcon('feedback')}</th>
                                                <th style={{ ...thStyle, cursor: 'default' }}>Действия</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {sorted.map((op, index) => {
                                                const callCount = parseInt(op.call_count) || 0;
                                                const planMeta = getAnalyticsEvaluationPlanMeta(op);
                                                const displayTarget = planMeta?.requiredCalls ?? getAnalyticsExpectedCalls(getAnalyticsCurrentWeek());
                                                const pct = displayTarget > 0 ? Math.round((callCount / displayTarget) * 100) : 0;
                                                const hasIssue = displayTarget > 0 && (callCount / displayTarget) * 100 < 95;
                                                return (
                                                    <tr key={op.id ?? index} style={{ background: index % 2 === 0 ? 'var(--surface)' : 'var(--surface-2)' }}>
                                                        <td style={tdStyle}>
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                                                <OperatorAvatar operator={op} />
                                                                <span style={{ fontWeight: 500 }}>{op.name}</span>
                                                            </div>
                                                        </td>
                                                        <td style={tdStyle}>{renderAnalyticsPlanContent(op, callCount)}</td>
                                                        <td style={tdStyle}>
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                                                <div style={{ width: 64, height: 5, background: 'var(--border)', borderRadius: 99, overflow: 'hidden', flexShrink: 0 }}>
                                                                    <div style={{ width: `${Math.min(pct, 100)}%`, height: '100%', background: getBarColor(pct), borderRadius: 99, transition: 'width 0.4s' }} />
                                                                </div>
                                                                <span style={{ fontSize: 12, fontWeight: 600, color: getBarColor(pct), tabularNums: true }}>{pct}%</span>
                                                            </div>
                                                        </td>
                                                        <td style={{ ...tdStyle, fontWeight: 600, color: getScoreColor(op.avg_score) }}>
                                                            {op.avg_score ? Number(op.avg_score).toFixed(2) : '—'}
                                                        </td>
                                                        <td style={tdStyle}>
                                                            <span style={{ fontWeight: 500 }}>{Number(op.feedback_count) || 0}</span>
                                                            <span style={{ color: 'var(--border)', margin: '0 3px' }}>/</span>
                                                            <span style={{ fontWeight: 500, color: Number(op.feedback_overdue_count) > 0 ? 'var(--red)' : 'var(--text-2)' }}>{Number(op.feedback_overdue_count) || 0}</span>
                                                        </td>
                                                        <td style={{ ...tdStyle, whiteSpace: 'nowrap' }}>
                                                            <div className={`analytics-actions ${isAdminRole ? 'analytics-actions-admin' : 'analytics-actions-compact'}`}>
                                                                {isAdminRole && hasIssue && (
                                                                    <button className="btn btn-sm" style={{ background: 'var(--amber-light,#fffbeb)', color: 'var(--amber,#d97706)', borderColor: 'var(--amber,#d97706)' }} onClick={() => analyticsNotifySv(analyticsEffectiveSvId, op.name, callCount, displayTarget)} disabled={analyticsLoading}>
                                                                        ⚠ Уведомить
                                                                    </button>
                                                                )}
                                                                {isAdminRole && !hasIssue && <span className="analytics-action-placeholder" aria-hidden="true" />}
                                                                <button className="btn btn-sm btn-primary" onClick={() => {
                                                                    const nextSupervisorId = Number(op?.supervisor_id ?? op?.sv_id ?? analyticsEffectiveSvId) || null;
                                                                    if (nextSupervisorId) setSelectedSupervisor(nextSupervisorId);
                                                                    setOperatorFromToken({ id: Number(op.id), name: op.name || '' });
                                                                    setSelectedMonth(analyticsMonth);
                                                                    setExpandedId(null);
                                                                    setActiveSection('journal');
                                                                }}>
                                                                    Оценки
                                                                </button>
                                                                {isAdminRole && (
                                                                    <button className="btn btn-sm" style={{ background: 'var(--accent-light)', color: 'var(--accent)', borderColor: 'var(--accent)' }} onClick={() => analyticsOpenAiFeedback(op.id, op.name, analyticsMonth)} disabled={analyticsAiModal.loading}>
                                                                        ОС от ИИ
                                                                    </button>
                                                                )}
                                                            </div>
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                        <tfoot style={{ background: 'var(--surface-2)', borderTop: '1px solid var(--border)' }}>
                                            <tr>
                                                <td style={{ ...tdStyle, fontWeight: 600 }}>Итого</td>
                                                <td style={tdStyle}>{totalPlanCalls > 0 ? `${totalCalls} / ${totalPlanCalls}` : totalCalls}</td>
                                                <td style={tdStyle}>
                                                    {(() => {
                                                        const withT = filteredOps.filter(o => (getAnalyticsEvaluationPlanMeta(o)?.requiredCalls ?? getAnalyticsExpectedCalls(getAnalyticsCurrentWeek())) > 0);
                                                        if (!withT.length) return <span style={{ color: 'var(--text-2)' }}>—</span>;
                                                        const ap = Math.round(withT.reduce((s, o) => { const t = getAnalyticsEvaluationPlanMeta(o)?.requiredCalls ?? getAnalyticsExpectedCalls(getAnalyticsCurrentWeek()); return s + ((parseInt(o.call_count)||0)/t)*100; }, 0) / withT.length);
                                                        return <span style={{ fontWeight: 600, color: getBarColor(ap) }}>{ap}%</span>;
                                                    })()}
                                                </td>
                                                <td style={{ ...tdStyle, fontWeight: 500 }}>{avgByScored == null ? '—' : avgByScored.toFixed(2)}</td>
                                                <td style={tdStyle}>
                                                    {filteredOps.reduce((s, o) => s + (Number(o.feedback_count)||0), 0)}
                                                    <span style={{ color: 'var(--border)', margin: '0 3px' }}>/</span>
                                                    <span style={{ fontWeight: 500, color: filteredOps.reduce((s, o) => s + (Number(o.feedback_overdue_count)||0), 0) > 0 ? 'var(--red)' : 'var(--text-2)' }}>
                                                        {filteredOps.reduce((s, o) => s + (Number(o.feedback_overdue_count)||0), 0)}
                                                    </span>
                                                </td>
                                                <td style={tdStyle} />
                                            </tr>
                                        </tfoot>
                                    </table>
                                </div>
                            );
                        })() : analyticsEffectiveSvId ? (
                            <p style={{ textAlign: 'center', color: 'var(--text-2)', padding: '32px 0', fontSize: 13 }}>Операторы не найдены для этого супервайзера.</p>
                        ) : (
                            <p style={{ textAlign: 'center', color: 'var(--text-2)', padding: '32px 0', fontSize: 13 }}>Выберите супервайзера для просмотра аналитики.</p>
                        )}
                    </div>
                </div>
            )}

            {/* ── Analytics AI Feedback Modal ── */}
            {analyticsAiModal.show && (
                <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
                    <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.4)', backdropFilter: 'blur(2px)' }} onClick={() => setAnalyticsAiModal(prev => ({ ...prev, show: false }))} />
                    <div style={{ position: 'relative', background: 'var(--surface)', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 640, maxHeight: '80vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                        <div className="panel-header">
                            <div>
                                <div style={{ fontSize: 11, color: 'var(--text-2)', marginBottom: 2 }}>{analyticsAiModal.title}</div>
                                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>Обратная связь от ИИ — сводка за месяц</div>
                            </div>
                            <button className="btn btn-sm btn-secondary" onClick={() => setAnalyticsAiModal(prev => ({ ...prev, show: false }))}>Закрыть</button>
                        </div>
                        <div style={{ padding: '20px 24px', overflowY: 'auto' }}>
                            {analyticsAiModal.loading && <p style={{ textAlign: 'center', color: 'var(--text-2)', padding: '32px 0' }}>Загрузка...</p>}
                            {analyticsAiModal.error && <pre style={{ color: 'var(--red)', fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{analyticsAiModal.error}</pre>}
                            {analyticsAiModal.result && (
                                <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.6 }}>
                                    {analyticsAiModal.result.summary?.text && <p style={{ marginBottom: 12 }}>{analyticsAiModal.result.summary.text}</p>}
                                    {analyticsAiModal.result.per_criterion?.map((c, i) => (
                                        <div key={i} style={{ marginBottom: 10, padding: '8px 12px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                                            <div style={{ fontWeight: 600, marginBottom: 4 }}>{c.criterion_name}</div>
                                            <div style={{ color: 'var(--text-2)' }}>{c.feedback}</div>
                                        </div>
                                    ))}
                                </div>
                            )}
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
            hydrateAuthSnapshot(data.auth || data.authentication || null);
            const nextState = { user: data.user || null, initialSelection: data.initialSelection || null };
            writeEmbedState(nextState);
            renderApp(nextState);
        } else if (data.type === 'CALL_EVALUATION_FOCUS') {
            if (typeof window.__callEvaluationFocus === 'function') {
                window.__callEvaluationFocus();
            }
        } else if (data.type === 'CALL_EVALUATION_SWITCH_SECTION') {
            if (typeof window.__callEvaluationSetSection === 'function') {
                window.__callEvaluationSetSection(data.section);
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

