import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { MessageSquare, X, Eye, EyeOff } from 'lucide-react';
import ChatThread, { ChatQuotesBar } from './ChatThread';

/* Просмотр переписки оценённого чата Chat2Desk (снапшот) с подсветкой цитат СВ.
 * Используется в «Мои оценки» чат-менеджера: оценка живёт в журнале (calls),
 * а этот док показывает сам чат по calls.c2d_snapshot_id + chat_quotes.
 * Сама лента вынесена в ChatThread — её же переиспользует полноэкранная
 * проверка низких оценок в «Учёте часов». */

export default function ChatSnapshotModal({ open, onClose, apiBaseUrl, withAccessTokenHeader, snapshotId, quotes = [], title = '', focusMessageId = null }) {
    const [snapshot, setSnapshot] = useState(null);
    const [error, setError] = useState('');
    const [hideService, setHideService] = useState(false);
    const threadRef = useRef(null);

    useEffect(() => {
        if (!open || !snapshotId) return;
        setSnapshot(null); setError('');
        const headers = withAccessTokenHeader ? withAccessTokenHeader() : {};
        axios.get(`${apiBaseUrl}/api/c2d_eval/snapshots/${snapshotId}`, { headers })
            .then((r) => setSnapshot(r.data.snapshot))
            .catch((e) => setError(e.response?.data?.error || 'Не удалось загрузить переписку (возможно, удалена по ретеншну)'));
    }, [open, snapshotId, apiBaseUrl, withAccessTokenHeader]);

    // Док не блокирует страницу: прокрутку не глушим (детали оценки с критериями
    // читаются слева), а лишь поджимаем контент классом на body. Esc закрывает.
    useEffect(() => {
        if (!open) return;
        document.body.classList.add('chat-dock-open');
        const onKeyDown = (e) => { if (e.key === 'Escape') onClose?.(); };
        window.addEventListener('keydown', onKeyDown);
        return () => {
            document.body.classList.remove('chat-dock-open');
            window.removeEventListener('keydown', onKeyDown);
        };
    }, [open, onClose]);

    if (!open) return null;

    return (
        <aside className="chat-dock">
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
                    <div className="min-w-0">
                        <div className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                            <MessageSquare size={16} className="text-blue-500" /> Переписка чата
                        </div>
                        {title && <div className="truncate text-xs text-slate-500">{title}</div>}
                    </div>
                    <div className="flex items-center gap-2">
                        {snapshot && (
                            <button onClick={() => setHideService((v) => !v)}
                                    className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1.5 text-[11.5px] font-semibold text-slate-600 transition hover:bg-slate-200">
                                {hideService ? <Eye size={12} /> : <EyeOff size={12} />}
                                {hideService ? 'Автоответы' : 'Без автоответов'}
                            </button>
                        )}
                        <button onClick={onClose} className="rounded-full p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
                            <X size={16} />
                        </button>
                    </div>
                </div>

                <ChatThread
                    ref={threadRef}
                    snapshot={snapshot}
                    loading={!snapshot && !error}
                    error={error}
                    quotes={quotes}
                    hideService={hideService}
                    focusMessageId={focusMessageId}
                />

                <ChatQuotesBar quotes={quotes} onSelect={(id) => threadRef.current?.scrollToMessage(id)} />
            </div>
        </aside>
    );
}
