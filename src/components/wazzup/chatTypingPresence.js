import { pilotChatKey } from './chatPilot.js';

const MAX_TTL_MS = 8000;
const TOMBSTONE_MS = 60000;
const MAX_SESSIONS = 512;

export function typingLabel(names) {
    if (!names.length) return '';
    if (names.length === 1) return `${names[0]} печатает…`;
    if (names.length === 2) return `${names[0]} и ${names[1]} печатают…`;
    return `${names[0]}, ${names[1]} и ещё ${names.length - 2} печатают…`;
}

// A heartbeat extends a deadline without rendering the workspace or even its
// indicator. Only changed labels notify subscribers, separately for each chat.
export function createChatTypingStore({ ownerId, now = Date.now,
    schedule = setTimeout, cancel = clearTimeout } = {}) {
    const entries = new Map(), labels = new Map(), listeners = new Map();
    let timer = null;
    const refresh = (keys) => {
        for (const key of keys) {
            const people = new Map();
            for (const entry of entries.values()) {
                if (entry.key === key && entry.typing && entry.expiresAt > now()) {
                    people.set(entry.userId, entry.authorName);
                }
            }
            const label = typingLabel([...people].sort(([a], [b]) => a.localeCompare(b)).map(([, name]) => name));
            if ((labels.get(key) || '') === label) continue;
            if (label) labels.set(key, label);
            else labels.delete(key);
            listeners.get(key)?.forEach((listener) => listener());
        }
    };
    const arm = () => {
        if (timer !== null) cancel(timer);
        timer = null;
        let deadline = Infinity;
        for (const entry of entries.values()) deadline = Math.min(deadline,
            entry.typing ? entry.expiresAt : entry.retainUntil);
        if (!Number.isFinite(deadline)) return;
        timer = schedule(() => {
            timer = null;
            const touched = new Set(), time = now();
            for (const [id, entry] of entries) {
                if (entry.retainUntil <= time) {
                    entries.delete(id); touched.add(entry.key);
                } else if (entry.typing && entry.expiresAt <= time) {
                    entry.typing = false; touched.add(entry.key);
                }
            }
            refresh(touched); arm();
        }, Math.max(0, deadline - now()));
    };
    return {
        subscribe(key, listener) {
            if (!listeners.has(key)) listeners.set(key, new Set());
            listeners.get(key).add(listener);
            return () => {
                listeners.get(key)?.delete(listener);
                if (!listeners.get(key)?.size) listeners.delete(key);
            };
        },
        snapshot: (key) => labels.get(key) || '',
        apply(events, serverTime) {
            const touched = new Set(), time = now();
            // SSE supplies its send time, so a workstation with an incorrect
            // clock still expires presence after the remaining server lease.
            const referenceTime = Number.isFinite(serverTime) ? serverTime : time;
            for (const event of events) {
                if (event.kind !== 'typing' || event.account !== 'op' || !event.channelId || !event.chatId
                    || !event.clientId || event.userId == null || typeof event.typing !== 'boolean'
                    || !Number.isFinite(event.emittedAt) || !Number.isFinite(event.expiresAt)) continue;
                const userId = String(event.userId);
                if (ownerId != null && userId === String(ownerId)) continue;
                const key = pilotChatKey('op', event);
                const id = JSON.stringify([key, userId, event.clientId]);
                const previous = entries.get(id);
                // Stops survive briefly as tombstones: delayed HTTP/PG events
                // and replayed starts must not resurrect an expired indicator.
                if (previous && (Number.isInteger(event.sequence) && Number.isInteger(previous.sequence)
                    ? event.sequence <= previous.sequence : event.emittedAt <= previous.emittedAt)) continue;
                if (event.typing && event.expiresAt <= referenceTime && !previous) continue;
                const expiresAt = time + Math.min(event.expiresAt - referenceTime, MAX_TTL_MS);
                entries.delete(id);
                entries.set(id, { key, userId, sequence: event.sequence, emittedAt: event.emittedAt,
                    authorName: String(event.authorName || 'Сотрудник').slice(0, 120),
                    typing: event.typing && expiresAt > time, expiresAt,
                    retainUntil: time + TOMBSTONE_MS });
                touched.add(key);
                while (entries.size > MAX_SESSIONS) {
                    const oldest = entries.keys().next().value;
                    touched.add(entries.get(oldest).key); entries.delete(oldest);
                }
            }
            if (touched.size) { refresh(touched); arm(); }
        },
        hide() {
            // A reconnect hides stale presence immediately but keeps sequence
            // tombstones, so an older start cannot undo a previously seen stop.
            const touched = new Set(labels.keys());
            for (const entry of entries.values()) entry.typing = false;
            refresh(touched); arm();
        },
        clear() {
            if (timer !== null) cancel(timer);
            timer = null;
            const touched = new Set(labels.keys());
            entries.clear(); refresh(touched);
        },
    };
}
