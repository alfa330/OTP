import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Loader2 } from 'lucide-react';

/* «Чаты по каналам» под строкой списка, как в Wazzup: в каких каналах этот
 * номер тоже писал — канал и время последнего сообщения; щелчок открывает чат.
 * Стрелка в строке есть, только если каналов больше одного (channelsCount,
 * wazzup/chat_list.py), поэтому блок не бывает списком из одного этого же чата.
 * Чаты номера отдаёт тот же список с точным фильтром по номеру. */
export default function ChatChannelsOfNumber({ id, chat, account, apiBaseUrl, headers, channelName, selected,
    onOpen, formatTime }) {
    const [rows, setRows] = useState(null);
    const [error, setError] = useState('');
    useEffect(() => {
        const controller = new AbortController();
        axios.get(`${apiBaseUrl}/api/wazzup/chats`, {
            headers: headers(), signal: controller.signal,
            params: { account, chat_id: chat.chatId, limit: 50 },
        }).then(({ data }) => setRows(data.items || []))
            .catch(() => { if (!controller.signal.aborted) setError('Не удалось загрузить каналы'); });
        return () => controller.abort();
        // headers — функция, пересоздаётся на каждый рендер раздела.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, account, chat.chatId]);
    const phone = chat.contactPhone || chat.chatId;
    return (
        <div id={id} role="group" aria-label="Чаты по каналам" data-testid="wazzup-chat-channels"
            className="mx-1.5 mb-1 rounded-xl bg-slate-50 px-1.5 py-1.5">
            <div className="px-1.5 pb-1 text-[11px] font-medium text-slate-400">Чаты по каналам</div>
            {rows === null && !error && (
                <div className="flex items-center gap-1.5 px-1.5 py-1 text-[12px] text-slate-400">
                    <Loader2 size={12} className="animate-spin" /> Загрузка…
                </div>
            )}
            {error && <div role="alert" className="px-1.5 py-1 text-[12px] text-rose-600">{error}</div>}
            {(rows || []).map((row) => {
                const current = Boolean(selected) && selected.channelId === row.channelId && selected.chatId === row.chatId;
                return (
                    <button key={`${row.channelId}:${row.chatId}`} type="button" onClick={() => onOpen(row)}
                        aria-current={current || undefined}
                        className={`flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left text-[12.5px] transition ${
                            current ? 'bg-blue-500/10 font-medium text-blue-700' : 'text-slate-700 hover:bg-slate-100'}`}>
                        <span className="min-w-0 flex-1 truncate">{channelName[row.channelId] || row.channelId}</span>
                        <span className="shrink-0 text-[11px] text-slate-400">{formatTime(row.lastMessageAt)}</span>
                    </button>
                );
            })}
            <div className="px-1.5 pt-1 text-[11px] text-slate-400">
                Телефон контакта: {/^\d+$/.test(String(phone)) ? `+${phone}` : phone}
            </div>
        </div>
    );
}
