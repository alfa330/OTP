import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
    POLL_ROUNDS, VOICE_BATCH, mergeVoiceTranscripts, pendingIds, pollDelay, unknownIds, voiceMessageIds,
    voiceTranscriptKey,
} from './voiceTranscripts';

/* Текст голосовых открытого чата: один запрос о голосовых ленты (новые сервер
 * тут же ставит в очередь), затем опрос только ждущих, пока не ответят. Живой
 * поток здесь не нужен: текст приходит через секунды после самого голосового,
 * и опрашивается лишь то, что видно в открытом чате. */
export default function useVoiceTranscripts({ account, chat, thread, apiBaseUrl, headers }) {
    const key = voiceTranscriptKey(account, chat);
    const ids = voiceMessageIds(thread);
    const signature = ids.join('|');
    const latest = useRef(null);
    latest.current = { key, account: account || 'op', channelId: chat?.channelId, chatId: chat?.chatId,
        ids, apiBaseUrl, headers };
    const run = useRef({ key: '', items: {}, busy: false, again: false, timer: null, rounds: 0, retry: [] });
    const [state, setState] = useState({ key: '', items: {} });

    const sync = useCallback(async () => {
        const current = run.current;
        const input = latest.current;
        if (!current.key || current.key !== input.key) return;
        if (current.busy) { current.again = true; return; }
        const retry = current.retry.splice(0, VOICE_BATCH);
        const ask = [...new Set([...pendingIds(input.ids, current.items),
            ...unknownIds(input.ids, current.items).reverse()])].slice(0, VOICE_BATCH - retry.length);
        const fresh = ask.filter((id) => !current.items[id]);
        if (!ask.length && !retry.length) return;
        if (fresh.length || retry.length) current.rounds = 0;
        clearTimeout(current.timer);
        current.timer = null;
        current.busy = true;
        try {
            const auth = input.headers;
            const { data } = await axios.post(`${input.apiBaseUrl}/api/wazzup/voice-transcripts`,
                { channelId: input.channelId, chatId: input.chatId, messageIds: ask, retry },
                { params: { account: input.account }, headers: typeof auth === 'function' ? auth() : auth,
                    timeout: 20000 });
            if (run.current !== current) return;
            const before = current.items;
            current.items = mergeVoiceTranscripts(current.items, [...ask, ...retry], data?.items);
            // Ответ пришёл — очередь сервера движется: отсчёт ожидания заново.
            if (ask.some((id) => before[id]?.status === 'pending' && current.items[id]?.status !== 'pending')) {
                current.rounds = 0;
            }
        } catch {
            if (run.current !== current) return;
            // Не спрашивать заново на каждое новое сообщение, пока ручка не отвечает;
            // ждущие опрашиваются дальше в пределах раундов.
            current.items = mergeVoiceTranscripts(current.items, fresh, []);
        } finally {
            current.busy = false;
        }
        setState({ key: current.key, items: current.items });
        // Длинная лента: следующая порция ещё не спрошена.
        if ((fresh.length && unknownIds(latest.current.ids, current.items).length) || current.retry.length) {
            current.again = true;
        }
        if (current.again) {
            current.again = false;
            sync();
            return;
        }
        const waiting = pendingIds(latest.current.ids, current.items);
        if (!waiting.length) return;
        if (current.rounds < POLL_ROUNDS) {
            current.timer = setTimeout(() => {
                current.timer = null;
                current.rounds += 1;
                sync();
            }, pollDelay(current.rounds));
            return;
        }
        // Не дождались: без вечного индикатора; при следующем открытии чата спросим снова.
        current.items = mergeVoiceTranscripts(current.items, waiting, []);
        setState({ key: current.key, items: current.items });
    }, []);

    useEffect(() => {
        const current = { key, items: {}, busy: false, again: false, timer: null, rounds: 0, retry: [] };
        run.current = current;
        setState({ key, items: {} });
        return () => {
            clearTimeout(current.timer);
            current.key = '';
        };
    }, [key]);

    useEffect(() => {
        if (key) sync();
    }, [key, signature, sync]);

    const retry = useCallback((messageId) => {
        const current = run.current;
        if (!current.key || !messageId) return;
        current.retry.push(messageId);
        current.items = { ...current.items, [messageId]: { status: 'pending', text: '' } };
        setState({ key: current.key, items: current.items });
        sync();
    }, [sync]);

    return { byId: state.key === key ? state.items : {}, retry };
}
