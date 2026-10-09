// Presence is transient: one request at a time, no polling and no draft content.
// A stop follows any outstanding pulse. Sequence numbers also protect against
// a timed-out HTTP request being processed after a newer request on the server.
export const TYPING_PULSE_MS = 3000;
export const TYPING_IDLE_MS = 2500;
// A new start after a stop (cleared field, blur, idle) — at most once a second.
export const TYPING_RESTART_MS = 1000;
const FAILURE_BACKOFF_MS = 5000;

export function createChatTypingSender({ send, now = Date.now, schedule = setTimeout, cancel = clearTimeout }) {
    let disposed = false;
    let blocked = false;
    let desired = false;
    let mayBeActive = false;
    let confirmed = false;   // the last positive request reached the server
    let pendingPulse = false;
    let running = false;
    let sequence = 0;
    let lastPositive = -Infinity;
    let retryAfter = 0;
    let idleTimer = null;
    let pulseTimer = null;

    const clearPulse = () => { if (pulseTimer !== null) cancel(pulseTimer); pulseTimer = null; };
    const clearIdle = () => { if (idleTimer !== null) cancel(idleTimer); idleTimer = null; };

    const pump = () => {
        if (running) return;
        const typing = desired && !disposed && !blocked;
        if (typing) {
            if (!pendingPulse) return;
            // A live indicator is renewed by the next edit after the pulse interval,
            // never by a timer: a timer heartbeat after the last keystroke lands just
            // before the idle stop and holds the next start back for seconds. Edits
            // pause less than the idle stop, so renewals stay well inside the 8 s TTL.
            const live = mayBeActive && confirmed;
            const delay = Math.max(lastPositive + (live ? TYPING_PULSE_MS : TYPING_RESTART_MS), retryAfter) - now();
            if (delay > 0) {
                if (!live && pulseTimer === null) pulseTimer = schedule(() => { pulseTimer = null; pump(); }, delay);
                return;
            }
        } else if (!mayBeActive) return;

        clearPulse();
        pendingPulse = false;
        mayBeActive = typing;
        if (typing) lastPositive = now();
        running = true;
        // Invoke immediately so the first keystroke does not wait for a debounce.
        let request;
        try { request = send({ typing, sequence: ++sequence }); }
        catch (error) { request = Promise.reject(error); }
        Promise.resolve(request).then(() => { confirmed = typing; }, (error) => {
            confirmed = false;
            retryAfter = now() + FAILURE_BACKOFF_MS;
            if ([401, 403].includes(error?.response?.status)) {
                blocked = true;
                desired = false;
                mayBeActive = false;
                pendingPulse = false;
                clearIdle();
                clearPulse();
            }
        }).finally(() => { running = false; pump(); });
    };

    const stop = () => {
        desired = false;
        pendingPulse = false;
        clearIdle();
        clearPulse();
        pump();
    };

    return {
        activity(value) {
            if (disposed || blocked) return;
            if (!String(value || '').trim()) { stop(); return; }
            desired = true;
            pendingPulse = true;
            clearIdle();
            idleTimer = schedule(stop, TYPING_IDLE_MS);
            pump();
        },
        stop,
        destroy() { disposed = true; stop(); },
    };
}
