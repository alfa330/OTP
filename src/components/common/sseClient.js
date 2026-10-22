/*
 * Клиент SSE на fetch: разбор кадров и держатель соединения с переподключением.
 *
 * EventSource не умеет наши заголовки авторизации, поэтому поток читается fetch'ем — как у
 * колокола, аукциона и чатов (NotificationsBell.jsx, ShiftAuctionView.jsx, useChatPilot.js).
 * Этот модуль — то же самое одним местом для новых потоков (раздел «АТС», «Табло ОП»), без
 * импортов: его разбор проверяет node-тест (tests/sse_client.test.mjs).
 *
 * Правила соединения:
 *   - сервер шлёт пульс (`: ping`) раз в 15–20 с; тишина дольше stallMs — соединение мёртвое,
 *     рвём и переподключаемся (прокси умеют держать полумёртвые соединения часами);
 *   - 503 — мест под потоки нет или канал выключен: ждём Retry-After (не меньше 30 с) и
 *     живём на обычном опросе — вызывающий узнаёт об этом по onState('off');
 *   - 401/403 — нет прав или протух вход: onState('denied'), повтор через пять минут;
 *   - обрыв — пауза растёт 2, 4, 8… до минуты, с разбросом, чтобы стены не стучались разом;
 *   - `bye` от сервера — плановое переподключение (предел жизни потока), сразу.
 */

export const createSseParser = () => {
    let buffer = '';
    let event = '';
    let data = [];
    return {
        /* Текст ответа → [{event, data}]. Комментарии — {event: '', data: текст}. */
        feed(text) {
            const out = [];
            buffer += text;
            let index = buffer.indexOf('\n');
            while (index >= 0) {
                const line = buffer.slice(0, index).replace(/\r$/, '');
                buffer = buffer.slice(index + 1);
                if (!line) {
                    if (event || data.length) out.push({ event: event || 'message', data: data.join('\n') });
                    event = '';
                    data = [];
                } else if (line.startsWith(':')) {
                    out.push({ event: '', data: line.slice(1).trim() });
                } else if (line.startsWith('event:')) {
                    event = line.slice(6).trim();
                } else if (line.startsWith('data:')) {
                    data.push(line.slice(5).replace(/^ /, ''));
                }
                index = buffer.indexOf('\n');
            }
            return out;
        },
    };
};

export const SSE_STALL_MS = 60000;

/*
 * Держатель соединения. onEvent(event, payload) — именованные кадры с разобранным JSON;
 * onState('connecting' | 'live' | 'off' | 'denied' | 'closed'). Возвращает {close}.
 */
export const runSseStream = ({
    url, getHeaders, onEvent, onState = () => {}, stallMs = SSE_STALL_MS,
    fetchImpl = (...args) => fetch(...args), random = Math.random, setTimer = setTimeout, clearTimer = clearTimeout,
}) => {
    let closed = false;
    let controller = null;
    let retryTimer = null;
    let stallTimer = null;
    let attempt = 0;

    const clearStall = () => {
        if (stallTimer) clearTimer(stallTimer);
        stallTimer = null;
    };
    const schedule = (delayMs) => {
        if (closed) return;
        retryTimer = setTimer(() => {
            retryTimer = null;
            connect();
        }, delayMs);
    };

    async function connect() {
        if (closed) return;
        onState('connecting');
        const current = new AbortController();
        controller = current;
        let stalled = false;
        const armStall = () => {
            clearStall();
            stallTimer = setTimer(() => {
                stalled = true;
                current.abort();
            }, stallMs);
        };
        armStall();
        let planned = false;
        try {
            const response = await fetchImpl(url, {
                headers: { ...(getHeaders ? getHeaders() : {}), Accept: 'text/event-stream' },
                signal: current.signal,
                credentials: 'include',
            });
            if (response.status === 503) {
                clearStall();
                const retryAfter = Number(response.headers?.get?.('Retry-After')) || 60;
                onState('off');
                schedule(Math.max(30, retryAfter) * 1000 + random() * 5000);
                return;
            }
            if (response.status === 401 || response.status === 403) {
                clearStall();
                onState('denied');
                schedule(5 * 60 * 1000);
                return;
            }
            if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            const parser = createSseParser();
            let reported = false;
            const reportLive = () => {
                if (reported) return;
                reported = true;
                attempt = 0;
                onState('live');
            };
            while (!closed) {
                const { value, done } = await reader.read();
                if (done) break;
                armStall();
                for (const frame of parser.feed(decoder.decode(value, { stream: true }))) {
                    if (!frame.event) {
                        if (frame.data.startsWith('connected')) reportLive();
                        continue;
                    }
                    if (frame.event === 'bye') { planned = true; continue; }
                    let payload = null;
                    try { payload = frame.data ? JSON.parse(frame.data) : null; } catch (parseError) { continue; }
                    // Первый настоящий кадр — тоже знак, что поток жив (не все шлют `: connected`).
                    reportLive();
                    onEvent(frame.event, payload);
                }
            }
        } catch (streamError) {
            if (closed || (streamError?.name === 'AbortError' && !stalled)) return;
        } finally {
            clearStall();
        }
        if (closed) return;
        onState('connecting');
        if (planned) { schedule(50); return; }
        attempt += 1;
        schedule(Math.min(60000, 1000 * (2 ** Math.min(attempt, 6))) + random() * 1000);
    }

    connect();
    return {
        close() {
            closed = true;
            clearStall();
            if (retryTimer) clearTimer(retryTimer);
            controller?.abort();
            onState('closed');
        },
    };
};
