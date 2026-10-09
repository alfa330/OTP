import { useEffect, useRef } from 'react';
import axios from 'axios';
import { newClientMessageId } from './chatPilot.js';
import { createChatTypingSender } from './chatTypingSender.js';

export default function useChatTypingSender({ apiBaseUrl, headers, chat, enabled }) {
    const senderRef = useRef(null);
    const latestHeaders = useRef(headers);
    latestHeaders.current = headers;
    const channelId = chat?.channelId;
    const chatId = chat?.chatId;

    useEffect(() => {
        if (!enabled || !channelId || !chatId) return undefined;
        const clientId = newClientMessageId();
        const sender = createChatTypingSender({
            send: (state) => axios.post(`${apiBaseUrl}/api/wazzup/pilot/typing`, {
                account: 'op', channelId, chatId, clientId, ...state,
            }, {
                headers: typeof latestHeaders.current === 'function' ? latestHeaders.current() : latestHeaders.current,
                timeout: 2500,
            }),
        });
        senderRef.current = sender;
        const hidden = () => { if (document.hidden) sender.stop(); };
        const leave = () => sender.stop();
        document.addEventListener('visibilitychange', hidden);
        window.addEventListener('pagehide', leave);
        window.addEventListener('blur', leave);
        return () => {
            document.removeEventListener('visibilitychange', hidden);
            window.removeEventListener('pagehide', leave);
            window.removeEventListener('blur', leave);
            senderRef.current = null;
            sender.destroy();
        };
    }, [apiBaseUrl, channelId, chatId, enabled]);

    // Only explicit edits call activity. Opening a saved draft is not typing.
    return {
        activity: (value) => { if (typeof document === 'undefined' || !document.hidden) senderRef.current?.activity(value); },
        stop: () => senderRef.current?.stop(),
    };
}
