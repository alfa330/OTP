// Kept outside the lazy picker: the initial chat bundle never imports its catalogue.
export function createEmojiLoader(importPicker) {
    let pending;
    return () => {
        if (!pending) pending = importPicker().catch((error) => { pending = null; throw error; });
        return pending;
    };
}

export const loadChatEmojiPicker = createEmojiLoader(() => import('./ChatEmojiPicker'));

export function warmChatEmojiPicker() {
    return loadChatEmojiPicker().then((module) => { module.preloadFirstEmojiImages?.(); return module; });
}

// Warm only in a visible, idle chat. Reduced-data connections stay strictly on demand.
export function scheduleEmojiWarmup(warm, host = globalThis) {
    const connection = host.navigator?.connection;
    if (connection?.saveData || /(^|-)2g$/.test(connection?.effectiveType || '')) return () => {};
    let disposed = false;
    let idleId;
    let timeoutId;
    const run = () => {
        if (disposed || host.document?.visibilityState === 'hidden') return;
        host.document?.removeEventListener('visibilitychange', schedule);
        Promise.resolve().then(warm).catch(() => {});
    };
    const schedule = () => {
        if (disposed || host.document?.visibilityState === 'hidden' || idleId !== undefined || timeoutId !== undefined) return;
        if (host.requestIdleCallback) idleId = host.requestIdleCallback(() => { idleId = undefined; run(); }, { timeout: 2500 });
        else timeoutId = host.setTimeout(() => { timeoutId = undefined; run(); }, 1200);
    };
    host.document?.addEventListener('visibilitychange', schedule);
    schedule();
    return () => {
        disposed = true;
        if (idleId !== undefined) host.cancelIdleCallback?.(idleId);
        if (timeoutId !== undefined) host.clearTimeout(timeoutId);
        host.document?.removeEventListener('visibilitychange', schedule);
    };
}
