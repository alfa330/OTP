import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { splitPilotEvents, pilotChatKey } from './chatPilot';

// One stream per visible chat screen. DB queries run only after changes, with
// bursts coalesced and at most one refresh in flight for each pane.
export default function useChatPilot({ apiBaseUrl, mayProcess, account, active, headers,
    selected, refreshThread, refreshList, onChanges, refreshUnread, refreshNotes, onTyping, clearTyping }) {
    const [capability, setCapability] = useState(null);
    const [connection, setConnection] = useState('connecting');
    const [visible, setVisible] = useState(() => typeof document === 'undefined' || !document.hidden);
    const latest = useRef({});
    latest.current = { headers, selected, refreshThread, refreshList, onChanges, refreshUnread, refreshNotes, onTyping, clearTyping };
    /* Где оборвался прошлый поток: эпоха процесса сервера и номер последнего
       кадра. Переподключение продолжает с этого места — сервер досылает всё,
       что вышло за разрыв, и перечитывать ленту, список, счётчик и заметки не
       нужно. Не узнал сервер место (рестарт, деплой, слишком давно) — сверка. */
    const resume = useRef({ epoch: null, seq: null });
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
        const connect = async (planned = false) => {
            if (stopped) return;
            controller = new AbortController();
            // A planned reconnect (the server re-checks access every ~2 minutes)
            // is not news: the connection note stays hidden through it.
            if (!planned) setConnection('connecting');
            let denied = false;
            let ended = false;
            let openedAt = 0;
            let watchdog;
            const handshake = setTimeout(() => controller.abort(), 20000);
            try {
                // axios performs the regular auth refresh before each SSE connection.
                const { data } = await axios.get(`${apiBaseUrl}/api/wazzup/pilot`, {
                    params: { account: 'op' }, headers: latest.current.headers(), signal: controller.signal, timeout: 15000,
                });
                if (!data.enabled) { denied = true; setCapability(data); return; }
                const { epoch, seq } = resume.current;
                const from = epoch && Number.isInteger(seq) ? `&epoch=${encodeURIComponent(epoch)}&after=${seq}` : '';
                const response = await fetch(`${apiBaseUrl}/api/wazzup/pilot/stream?account=op${from}`, {
                    headers: { ...latest.current.headers(), Accept: 'text/event-stream' },
                    credentials: 'include', signal: controller.signal, cache: 'no-store',
                });
                if (!response.ok || !response.body) {
                    denied = response.status === 401 || response.status === 403;
                    throw new Error('Stream unavailable');
                }
                const reader = response.body.getReader();
                clearTimeout(handshake);
                openedAt = Date.now();
                const decoder = new TextDecoder();
                let buffer = '';
                let lastByteAt = Date.now();
                watchdog = setInterval(() => {
                    if (Date.now() - lastByteAt > 65000) controller.abort();
                }, 10000);
                try {
                    while (!stopped) {
                        const { done, value } = await reader.read();
                        if (done || stopped) { ended = done; break; }
                        lastByteAt = Date.now();
                        const parsed = splitPilotEvents(buffer + decoder.decode(value, { stream: true }));
                        buffer = parsed.buffer;
                        for (const frame of parsed.frames) {
                            if (/^\d+$/.test(frame.id || '')) resume.current.seq = Number(frame.id);
                            const payload = JSON.parse(frame.data || '{}');
                            if (frame.event === 'unavailable') {
                                latest.current.clearTyping?.();
                                setConnection('reconnecting');
                            } else if (frame.event === 'connected' || frame.event === 'reload') {
                                failures = 0;
                                setConnection(payload.ready === false ? 'reconnecting' : 'live');
                                if (frame.event === 'connected') resume.current = {
                                    epoch: typeof payload.epoch === 'string' ? payload.epoch : null,
                                    seq: Number.isInteger(payload.seq) ? payload.seq : null,
                                };
                                // Continued where the last stream stopped: the gap was replayed.
                                if (!(frame.event === 'connected' && payload.resumed === true)) {
                                    latest.current.clearTyping?.();
                                    reconcile();
                                }
                            } else if (frame.event === 'typing') {
                                // Presence has its own frame and never reaches message handling.
                                latest.current.onTyping?.(payload.typing || [], payload.serverTime);
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
                    // The server closed a healthy stream itself: reconnect at once and
                    // quietly. A stream that dies young is a fault and backs off.
                    const replan = !denied && ended && Date.now() - openedAt >= 5000;
                    if (replan) reconnectTimer = setTimeout(() => connect(true), 0);
                    else {
                        latest.current.clearTyping?.();
                        setConnection(denied ? 'unavailable' : 'reconnecting');
                        if (!denied) reconnectTimer = setTimeout(connect,
                            Math.min(15000, 1000 * 2 ** Math.min(failures++, 4)) + Math.random() * 300);
                    }
                }
            }
        };
        connect();
        return () => {
            stopped = true;
            latest.current.clearTyping?.();
            controller?.abort();
            clearTimeout(reconnectTimer);
            // A re-read that was asked for and has not finished (the tab was hidden
            // within its delay) must not be skipped by the next, resumed stream.
            if (Object.values(lanes).some((lane) => lane.dirty || lane.running || lane.timer)) {
                resume.current = { epoch: null, seq: null };
            }
            Object.values(lanes).forEach((lane) => clearTimeout(lane.timer));
        };
    }, [enabled, active, visible, apiBaseUrl]);
    return { enabled, capability, connection: !active || !visible ? 'paused' : connection };
}
