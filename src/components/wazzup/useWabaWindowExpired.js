import { useEffect, useState } from 'react';

export const WABA_WINDOW_MS = 24 * 60 * 60 * 1000;
const MAX_TIMEOUT_MS = 2 ** 31 - 1;

function timestamp(value) {
    if (value == null || typeof value === 'boolean' || (typeof value === 'string' && !value.trim())) return null;
    if (typeof value !== 'number' && typeof value !== 'string' && !(value instanceof Date)) return null;
    const parsed = new Date(value).getTime();
    return Number.isFinite(parsed) ? parsed : null;
}

// A delayed webhook has its original customer timestamp in wazzupDt. An
// incoming message still opened the window even if it was subsequently deleted.
export function latestInboundTime(messages, baseline = null) {
    let latest = timestamp(baseline);
    for (const message of messages || []) {
        if (!message || message.isEcho !== false || message._note || message._day) continue;
        const sent = timestamp(message.wazzupDt || message.dt);
        if (sent !== null && (latest === null || sent > latest)) latest = sent;
    }
    return latest;
}

export function useWabaWindowExpired(enabled, lastInboundAt) {
    const inbound = timestamp(lastInboundAt);
    const deadline = inbound === null ? null : inbound + WABA_WINDOW_MS;
    const [, setClock] = useState(Date.now);

    useEffect(() => {
        if (!enabled || deadline === null) return undefined;
        let timer;
        let disposed = false;
        const refresh = () => {
            if (disposed) return;
            clearTimeout(timer);
            const now = Date.now();
            setClock(now);
            if (now < deadline) timer = setTimeout(refresh, Math.min(deadline - now, MAX_TIMEOUT_MS));
        };
        const onVisible = () => {
            if (typeof document === 'undefined' || document.visibilityState !== 'hidden') refresh();
        };
        refresh();
        if (typeof window !== 'undefined') window.addEventListener?.('focus', refresh);
        if (typeof document !== 'undefined') document.addEventListener?.('visibilitychange', onVisible);
        return () => {
            disposed = true;
            clearTimeout(timer);
            if (typeof window !== 'undefined') window.removeEventListener?.('focus', refresh);
            if (typeof document !== 'undefined') document.removeEventListener?.('visibilitychange', onVisible);
        };
    }, [enabled, deadline]);

    // Compute from current props on every render: switching chats or receiving
    // a new incoming message must hide the old hint before effects run.
    return Boolean(enabled && deadline !== null && Date.now() >= deadline);
}

export default useWabaWindowExpired;
