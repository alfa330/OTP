import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { splitPilotEvents, pilotChatKey } from './chatPilot';

// One stream per visible chat screen. DB queries run only after changes, with
// bursts coalesced and at most one refresh in flight for each pane.
export default function useChatPilot({ apiBaseUrl, mayProcess, account, active, headers,
    selected, refreshThread, refreshList, onChanges, refreshUnread, refreshNotes }) {
    const [capability, setCapability] = useState(null);
    const [connection, setConnection] = useState('connecting');
    const [visible, setVisible] = useState(() => typeof document === 'undefined' || !document.hidden);
    const latest = useRef({});
    latest.current = { headers, selected, refreshThread, refreshList, onChanges, refreshUnread, refreshNotes };
    // mayProcess is the caller's hint about who can process chats at all; it only
    // spares everyone else the capability request. The server's answer decides.
    const eligible = Boolean(mayProcess) && account === 'op';
    useEffect(() => {
        const change = () => setVisible(!document.hidden);
        document.addEventListener('visibilitychange', change);
        return () => document.removeEventListener('visibilitychange', change);
    }, []);
    useEffect(() => {
        setCapability(null);
        if (!eligible) return undefined;
        const controller = new AbortController();
        axios.get(`${apiBaseUrl}/api/wazzup/pilot`, {
            params: { account: 'op' }, headers: latest.current.headers(), signal: controller.signal, timeout: 15000,
        }).then(({ data }) => setCapability(data)).catch((error) => {
            if (!controller.signal.aborted) setConnection('unavailable');
        });
        return () => controller.abort();
    }, [eligible, apiBaseUrl]);

    const enabled = eligible && Boolean(capability?.enabled);
    useEffect(() => {
        if (!enabled || !active || !visible) return undefined;
        let stopped = false;
        let controller;
        let reconnectTimer;
        let failures = 0;
        const lanes = {
            thread: { dirty: false, running: false, timer: null, delay: 350 },
            list: { dirty: false, running: false, timer: null, delay: 1000 },
            unread: { dirty: false, running: false, timer: null, delay: 200 },
            notes: { dirty: false, running: false, timer: null, delay: 350 },
        };
        const enqueue = (name) => {
            const lane = lanes[name];
            lane.dirty = true;
            if (stopped || lane.running || lane.timer) return;
            lane.timer = setTimeout(async () => {
                lane.timer = null;
                if (stopped) return;
                lane.dirty = false;
                lane.running = true;
                try {
                    const result = await latest.current[{ thread: 'refreshThread', list: 'refreshList', unread: 'refreshUnread', notes: 'refreshNotes' }[name]]?.();
                    if (result === false) lane.dirty = true; // History/page request still in flight.
                } catch (error) {
                    if (stopped) return;
                    if ([401, 403].includes(error.response?.status)) {
                        if (!stopped) { setConnection('unavailable'); setCapability(null); }
                        return;
                    }
                    if (!stopped) setConnection('reconnecting');
                    lane.dirty = true;
                    // A DB/network outage must not become a tight request loop.
                    lane.timer = setTimeout(() => {
                        lane.timer = null;
                        if (!stopped) enqueue(name);
                    }, 5000);
                } finally {
                    lane.running = false;
                    if (lane.dirty && !stopped) enqueue(name);
                }
            }, lane.delay);
        };
        const reconcile = () => { enqueue('thread'); enqueue('list'); enqueue('unread'); enqueue('notes'); };
        const connect = async () => {
            if (stopped) return;
            controller = new AbortController();
            setConnection('connecting');
            let denied = false;
            let watchdog;
            const handshake = setTimeout(() => controller.abort(), 20000);
            try {
                // axios performs the regular auth refresh before each SSE connection.
                const { data } = await axios.get(`${apiBaseUrl}/api/wazzup/pilot`, {
                    params: { account: 'op' }, headers: latest.current.headers(), signal: controller.signal, timeout: 15000,
                });
                if (!data.enabled) { denied = true; setCapability(data); return; }
                const response = await fetch(`${apiBaseUrl}/api/wazzup/pilot/stream?account=op`, {
                    headers: { ...latest.current.headers(), Accept: 'text/event-stream' },
                    credentials: 'include', signal: controller.signal, cache: 'no-store',
                });
                if (!response.ok || !response.body) {
                    denied = response.status === 401 || response.status === 403;
                    throw new Error('Stream unavailable');
                }
                const reader = response.body.getReader();
                clearTimeout(handshake);
                const decoder = new TextDecoder();
                let buffer = '';
                let lastByteAt = Date.now();
                watchdog = setInterval(() => {
                    if (Date.now() - lastByteAt > 65000) controller.abort();
                }, 10000);
                try {
                    while (!stopped) {
                        const { done, value } = await reader.read();
                        if (done || stopped) break;
                        lastByteAt = Date.now();
                        const parsed = splitPilotEvents(buffer + decoder.decode(value, { stream: true }));
                        buffer = parsed.buffer;
                        for (const frame of parsed.frames) {
                            const payload = JSON.parse(frame.data || '{}');
                            if (frame.event === 'unavailable') {
                                setConnection('reconnecting');
                            } else if (frame.event === 'connected' || frame.event === 'reload') {
                                failures = 0;
                                setConnection(payload.ready === false ? 'reconnecting' : 'live');
                                reconcile();
                            } else if (frame.event === 'change') {
                                setConnection('live');
                                const fallback = latest.current.onChanges?.(payload.changes || []);
                                if (fallback?.notes) enqueue('notes');
                                if (fallback ? fallback.list : payload.changes?.some((event) => event.affectsList !== false)) enqueue('list');
                                const key = pilotChatKey('op', latest.current.selected);
                                if (fallback ? fallback.thread : payload.changes?.some((event) => pilotChatKey('op', event) === key)) {
                                    enqueue('thread');
                                }
                            }
                        }
                    }
                } finally {
                    await reader.cancel().catch(() => {});
                    reader.releaseLock();
                }
            } catch (error) {
                denied ||= error.response?.status === 401 || error.response?.status === 403;
            } finally {
                clearInterval(watchdog);
                clearTimeout(handshake);
                if (!stopped) {
                    setConnection(denied ? 'unavailable' : 'reconnecting');
                    if (!denied) reconnectTimer = setTimeout(connect,
                        Math.min(15000, 1000 * 2 ** Math.min(failures++, 4)) + Math.random() * 300);
                }
            }
        };
        connect();
        return () => {
            stopped = true;
            controller?.abort();
            clearTimeout(reconnectTimer);
            Object.values(lanes).forEach((lane) => clearTimeout(lane.timer));
        };
    }, [enabled, active, visible, apiBaseUrl]);
    return { enabled, capability, connection: !active || !visible ? 'paused' : connection };
}
