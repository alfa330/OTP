import React from 'react';
import { StickyNote } from 'lucide-react';

export default function ChatInternalNote({ note }) {
    const created = new Date(note.createdAt);
    const validDate = Number.isFinite(created.getTime());
    return (
        <div className="mx-auto w-fit min-w-0 max-w-[92%] sm:max-w-[78%] lg:max-w-[640px]"
            data-internal-note-id={note.id}>
            <div className="mb-1 px-2 text-xs font-medium text-slate-500">{note.authorName || 'Сотрудник'}</div>
            <div className="wazzup-internal-note rounded-2xl border border-[#e9e3d5] bg-[#faf7ef] px-3 py-2 text-slate-800">
                <div className="mb-1 flex items-center gap-1 text-[11px] font-medium leading-4 text-slate-500">
                    <StickyNote size={11} aria-hidden="true" />
                    <span>Внутренний комментарий</span>
                </div>
                <div className="whitespace-pre-wrap break-words text-[15px] leading-relaxed [overflow-wrap:anywhere]">{note.text}</div>
                <div className="mt-0.5 text-right text-[11px] leading-4 text-slate-500"
                    title={validDate ? created.toLocaleString('ru-RU') : undefined}>
                    {validDate ? created.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : ''}
                </div>
            </div>
        </div>
    );
}
